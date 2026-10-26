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
        version = "0.4.0";
        src = nixpkgs.lib.fileset.toSource {
          root = ./.;
          fileset = nixpkgs.lib.fileset.unions [ ./src ./Cargo.toml ./Cargo.lock ];
        };
        cargoLock.lockFile = ./Cargo.lock;
        doCheck = true;
      };
      vmControllerFor = pkgs: pkgs.rustPlatform.buildRustPackage {
        pname = "veil-arena-vm-controller";
        version = "0.4.0";
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
        mkWardenPreflightRunner = { hostSystem, hostUid }:
          let pkgs = pkgsFor hostSystem; in pkgs.writeShellApplication {
            name = "arena-warden-preflight";
            runtimeInputs = [ pkgs.python314 pkgs.coreutils pkgs.qemu (vmControllerFor pkgs) ];
            text = ''exec python3 ${./scripts}/warden_preflight_host.py ${toString hostUid} "$@"'';
          };
        mkWardenPreflightGuest = { hostUid, wardenPackage, wardenVerifier, guestSystem ? "aarch64-linux" }:
          import ./environments/vm/warden-preflight.nix { inherit nixpkgs hostUid wardenPackage wardenVerifier guestSystem; };
        mkWardenGuest = { hostUid, wardenInputs, guestSystem ? "aarch64-linux" }:
          import ./environments/vm/warden.nix { inherit nixpkgs hostUid wardenInputs guestSystem; };
        mkWardenRunner = { hostSystem, hostUid }:
          let pkgs = pkgsFor hostSystem; in pkgs.writeShellApplication {
            name = "arena-warden";
            runtimeInputs = [ pkgs.python314 pkgs.coreutils pkgs.qemu (vmControllerFor pkgs) ];
            text = ''exec python3 ${./scripts}/warden_host.py ${toString hostUid} "$@"'';
          };
        mkK3sGuest = { hostUid, f2Role ? null, keeperSource ? null }: import ./environments/vm/k3s.nix { inherit nixpkgs hostUid f2Role keeperSource; };
        mkF4Runner = { hostSystem, hostUid }:
          let pkgs = pkgsFor hostSystem; in assert pkgs.qemu.version == "11.1.1"; pkgs.writeShellApplication {
            name = "arena-k3s-f4";
            runtimeInputs = [ pkgs.python314 pkgs.coreutils pkgs.qemu (vmControllerFor pkgs) ];
            text = ''exec python3 ${./scripts}/k3s_f4_host.py ${toString hostUid} "$@"'';
          };
        mkKeeperRunner = { hostSystem, hostUid }:
          let pkgs = pkgsFor hostSystem; in assert pkgs.qemu.version == "11.1.1"; pkgs.writeShellApplication {
            name = "arena-keeper";
            runtimeInputs = [ pkgs.python314 pkgs.coreutils pkgs.qemu (vmControllerFor pkgs) ];
            text = ''exec python3 ${./scripts}/k3s_f3_host.py ${toString hostUid} "$@"'';
          };
        mkK3sF2Runner = { hostSystem, hostUid }:
          let pkgs = pkgsFor hostSystem; in assert pkgs.qemu.version == "11.1.1"; pkgs.writeShellApplication {
            name = "arena-k3s-f2";
            runtimeInputs = [ pkgs.python314 pkgs.coreutils pkgs.qemu (vmControllerFor pkgs) ];
            text = ''exec python3 ${./scripts}/k3s_f2_host.py ${toString hostUid} "$@"'';
          };
        mkK3sRunner = { hostSystem, hostUid }:
          let pkgs = pkgsFor hostSystem; in assert pkgs.qemu.version == "11.1.1"; pkgs.writeShellApplication {
            name = "arena-k3s";
            runtimeInputs = [ pkgs.python314 pkgs.coreutils pkgs.qemu (vmControllerFor pkgs) ];
            text = ''exec python3 ${./scripts}/k3s_host.py ${toString hostUid} "$@"'';
          };
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
