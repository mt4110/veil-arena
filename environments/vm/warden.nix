# Dedicated offline Warden guest. Existing Warden scope guards remain unchanged.
{ nixpkgs, hostUid, wardenInputs, guestSystem ? "aarch64-linux" }:
assert builtins.isInt hostUid && hostUid > 0;
assert guestSystem == "aarch64-linux";
let
  pkgs = import nixpkgs { system = guestSystem; };
  lib = nixpkgs.lib;
  inputs = builtins.path { path = wardenInputs; name = "arena-warden-inputs"; };
  # The owned cached CLI uses the older Warden Nix libc. Rebase only its ELF
  # loader/libraries in a new derivation; retain original bytes and both hashes.
  cli = pkgs.stdenv.mkDerivation {
    pname = "arena-warden-cli"; version = "0.1.0";
    src = inputs; dontUnpack = true;
    nativeBuildInputs = [ pkgs.autoPatchelfHook ];
    buildInputs = [ pkgs.stdenv.cc.cc.lib pkgs.openssl ];
    installPhase = ''mkdir -p $out/bin; cp $src/veil-warden $out/bin/veil-warden; chmod 755 $out/bin/veil-warden'';
  };
  system = lib.nixosSystem {
    system = guestSystem;
    modules = [ ({ modulesPath, ... }: {
      imports = [ (modulesPath + "/virtualisation/qemu-vm.nix") ];
      system.stateVersion = "26.05";
      networking.hostName = "veil-warden-sandbox";
      networking.useDHCP = false;
      services.openssh.enable = false;
      nix.enable = false;
      boot.kernelPackages = pkgs.linuxPackages_6_12;
      boot.kernelParams = [ "arena.warden=1" ];
      boot.initrd.availableKernelModules = [ "virtio_blk" "virtio_pci" "9p" "9pnet_virtio" "erofs" ];
      virtualisation = {
        diskImage = null; graphics = false; memorySize = 4096; cores = 2;
        useNixStoreImage = true; mountHostNixStore = false;
        writableStore = false; useHostCerts = false;
        sharedDirectories = lib.mkForce { out = { source = "$ARENA_VM_OUT"; target = "/out"; writable = true; }; };
        qemu.networkingOptions = lib.mkForce [ "-nic none" ];
      };
      virtualisation.fileSystems."/out" = { fsType = lib.mkForce "9p";
        options = lib.mkForce [ "trans=virtio" "version=9p2000.L" "msize=1048576" "rw" "x-systemd.requires=modprobe@9pnet_virtio.service" ]; };
      systemd.slices.warden = { description = "Arena Warden test parent"; };
      systemd.slices.warden-test = { description = "Arena Warden synthetic clients only"; };
      environment.systemPackages = [ pkgs.python314 pkgs.bpftools pkgs.iproute2 pkgs.coreutils ];
      systemd.services.arena-warden = {
        wantedBy = [ "multi-user.target" ];
        after = [ "out.mount" "warden-test.slice" ];
        requires = [ "out.mount" "warden-test.slice" ];
        path = [ pkgs.python314 pkgs.bpftools pkgs.systemd pkgs.coreutils pkgs.iproute2 ];
        environment = { PYTHONDONTWRITEBYTECODE = "1"; PYTHONNOUSERSITE = "1"; };
        serviceConfig = { Type = "oneshot"; User = "root"; RuntimeMaxSec = 180;
          TimeoutStartSec = 180; TimeoutStopSec = 10; KillMode = "control-group"; };
        script = ''
          cat /proc/sys/kernel/random/boot_id > /out/guest-boot-id.txt
          cp ${inputs}/inputs.json /out/warden-inputs.json
          sha256sum ${cli}/bin/veil-warden ${inputs}/veil-warden-ebpf > /out/runtime-inputs.sha256
          ${pkgs.python314}/bin/python3 ${../../scripts/warden_guest.py} ${cli}/bin/veil-warden ${inputs} /out
        '';
        postStop = ''printf '%s\n' "$SERVICE_RESULT" > /out/service-result.txt'';
        unitConfig.OnSuccess = "arena-poweroff.service";
        unitConfig.OnFailure = "arena-poweroff.service";
      };
      systemd.services.arena-poweroff = { serviceConfig.Type = "oneshot"; script = "${pkgs.systemd}/bin/systemctl poweroff"; };
    }) ];
  };
  cfg = system.config;
  closure = pkgs.closureInfo { rootPaths = [ cfg.system.build.toplevel ]; };
in pkgs.runCommand "arena-warden-bundle" {} (''
  mkdir -p $out
'' + import (nixpkgs + "/nixos/lib/erofs-store-image.nix") {
  hostPkgs = pkgs; storePaths = "${closure}/store-paths"; label = "nix-store"; destination = "$out/store.img";
} + ''
  cp ${cfg.system.build.kernel}/${cfg.system.boot.loader.kernelFile} $out/Image
  cp ${cfg.system.build.initialRamdisk}/${cfg.system.boot.loader.initrdFile} $out/initrd
  cp ${cfg.system.build.toplevel}/kernel-params $out/kernel-params
  printf '%s\n' '${cfg.system.build.toplevel}' > $out/toplevel
  cp ${inputs}/inputs.json $out/inputs.json
  cd $out
  sha256sum store.img Image initrd kernel-params toplevel inputs.json > SHA256SUMS
'')
