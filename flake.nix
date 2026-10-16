{
  description = "Veil Arena: pinned development tools and offline Linux VM";

  inputs.nixpkgs.url = "https://releases.nixos.org/nixpkgs/nixpkgs-26.11pre1088030.8edc0c72e3a3/nixexprs.tar.zst";

  outputs = { self, nixpkgs }:
    let
      systems = [ "aarch64-darwin" "aarch64-linux" "x86_64-linux" ];
      eachSystem = nixpkgs.lib.genAttrs systems;
      # This Rust patch release ships the Cargo 1.98.0 binary in Nixpkgs.
      versions = { rust = "1.98.1"; cargo = "1.98.0"; python = "3.14.7"; kernel = "6.12.112"; };
      pkgsFor = system: import nixpkgs { inherit system; };
      toolsFor = pkgs:
        assert pkgs.rustc.version == versions.rust;
        assert pkgs.cargo.version == versions.rust;
        assert pkgs.python314.version == versions.python;
        [ pkgs.rustc pkgs.cargo pkgs.rustfmt pkgs.clippy pkgs.python314 pkgs.git pkgs.stdenv.cc ];
      tcReferenceFor = pkgs:
        assert pkgs.llvmPackages.clang-unwrapped.version == "21.1.8";
        pkgs.runCommand "arena-tc-reference" {} ''
        mkdir -p $out
        ${pkgs.llvmPackages.clang-unwrapped}/bin/clang -O2 -g -target bpfel -Wall -Werror \
          -fdebug-prefix-map=/nix/store=/arena/build-inputs -I${pkgs.linuxHeaders}/include \
          -c ${./reference/tc_ingress.c} -o $out/tc_ingress.o
        ${pkgs.llvmPackages.clang-unwrapped}/bin/clang -O2 -g -target bpfel -Wall -Werror \
          -fdebug-prefix-map=/nix/store=/arena/build-inputs -I${pkgs.linuxHeaders}/include \
          -c ${./reference/tc_load_reject.c} -o $out/tc_load_reject.o
        ${pkgs.llvmPackages.clang-unwrapped}/bin/clang --version | sed -n '1p' > $out/clang-version.txt
        sha256sum $out/tc_ingress.o $out/tc_load_reject.o > $out/object.sha256
      '';
      arenaFor = pkgs: pkgs.rustPlatform.buildRustPackage {
        pname = "veil-arena";
        version = "0.2.0";
        src = nixpkgs.lib.fileset.toSource {
          root = ./.;
          fileset = nixpkgs.lib.fileset.unions [ ./src ./Cargo.toml ./Cargo.lock ];
        };
        cargoLock.lockFile = ./Cargo.lock;
        doCheck = true;
      };
      vmControllerFor = pkgs: pkgs.rustPlatform.buildRustPackage {
        pname = "veil-arena-vm-controller";
        version = "0.2.0";
        src = nixpkgs.lib.fileset.toSource {
          root = ./tools/vm-controller;
          fileset = nixpkgs.lib.fileset.unions [ ./tools/vm-controller/src
            ./tools/vm-controller/Cargo.toml ./tools/vm-controller/Cargo.lock ];
        };
        cargoLock.lockFile = ./tools/vm-controller/Cargo.lock;
        doCheck = true;
      };
    in {
      lib = {
        inherit versions;
        mkNetnsBundleVm = { hostSystem, hostUid, guestBundle }:
          import ./environments/vm/offline.nix {
            inherit nixpkgs versions toolsFor arenaFor vmControllerFor tcReferenceFor hostSystem hostUid guestBundle;
            blackholeSource = null;
            accelerator = "tcg";
            scenario = "netns-batch";
          };
        mkOfflineVm = { hostSystem, blackholeSource, hostUid, accelerator ? "tcg" }:
          import ./environments/vm/offline.nix {
            inherit nixpkgs versions toolsFor arenaFor vmControllerFor tcReferenceFor hostSystem blackholeSource hostUid accelerator;
          };
        mkNetnsVm = { hostSystem, hostUid, accelerator ? "tcg", omitServerRoute ? false }:
          import ./environments/vm/offline.nix {
            inherit nixpkgs versions toolsFor arenaFor vmControllerFor tcReferenceFor hostSystem hostUid accelerator omitServerRoute;
            blackholeSource = null;
            scenario = "netns-baseline";
          };
        mkNetnsBatchVm = { hostSystem, hostUid, accelerator ? "tcg" }:
          import ./environments/vm/offline.nix {
            inherit nixpkgs versions toolsFor arenaFor vmControllerFor tcReferenceFor hostSystem hostUid accelerator;
            blackholeSource = null;
            scenario = "netns-batch";
          };
      };
      devShells = eachSystem (system: let pkgs = pkgsFor system; in {
        default = pkgs.mkShell {
          packages = toolsFor pkgs;
          shellHook = ''
            export PYTHONNOUSERSITE=1
            echo "Veil Arena: Rust ${versions.rust}, Python ${versions.python} (${system})"
          '';
        };
      });
      packages = eachSystem (system: let pkgs = pkgsFor system; in {
        default = arenaFor pkgs;
        vm-controller = vmControllerFor pkgs;
        qemu = pkgs.qemu;
      });
      checks = eachSystem (system: {
        arena = self.packages.${system}.default;
        vm-controller = self.packages.${system}.vm-controller;
        versions = let pkgs = pkgsFor system; in
          assert (builtins.fromTOML (builtins.readFile ./rust-toolchain.toml)).toolchain.channel == versions.rust;
          assert (builtins.fromTOML (builtins.readFile ./Cargo.toml)).package.rust-version == versions.rust;
          assert (builtins.fromTOML (builtins.readFile ./tools/vm-controller/Cargo.toml)).package.rust-version == versions.rust;
          assert nixpkgs.lib.removeSuffix "\n" (builtins.readFile ./.python-version) == versions.python;
          pkgs.runCommand "arena-version-contract" {} ''touch $out'';
      });
    };
}
