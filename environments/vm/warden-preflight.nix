# A separate non-loading scenario; never imports the policy/attach guest.
{
  nixpkgs,
  hostUid,
  wardenPackage,
  wardenVerifier,
  guestSystem ? "aarch64-linux",
}:
assert builtins.isInt hostUid && hostUid > 0;
assert builtins.elem guestSystem [
  "aarch64-linux"
  "x86_64-linux"
];
let
  pkgs = import nixpkgs { system = guestSystem; };
  lib = nixpkgs.lib;
  system = lib.nixosSystem {
    system = guestSystem;
    modules = [
      ({ modulesPath, ... }: {
        imports = [ (modulesPath + "/virtualisation/qemu-vm.nix") ];
        system.stateVersion = "26.05";
        # Intentionally does not satisfy Warden's loader hostname guard.
        networking.hostName = "arena-warden-preflight";
        networking.useDHCP = false;
        services.openssh.enable = false;
        nix.enable = false;
        boot.kernelPackages = pkgs.linuxPackages_6_12;
        boot.kernelParams = [ "arena.warden-preflight=1" ];
        boot.initrd.availableKernelModules = [
          "virtio_blk"
          "virtio_pci"
          "9p"
          "9pnet_virtio"
          "erofs"
        ];
        virtualisation = {
          diskImage = null;
          graphics = false;
          memorySize = 4096;
          cores = 2;
          useNixStoreImage = true;
          mountHostNixStore = false;
          writableStore = false;
          useHostCerts = false;
          sharedDirectories = lib.mkForce {
            out = {
              source = "$ARENA_VM_OUT";
              target = "/out";
              writable = true;
            };
          };
          qemu.networkingOptions = lib.mkForce [ "-nic none" ];
        };
        virtualisation.fileSystems."/out" = {
          fsType = lib.mkForce "9p";
          options = lib.mkForce [
            "trans=virtio"
            "version=9p2000.L"
            "msize=1048576"
            "rw"
            "x-systemd.requires=modprobe@9pnet_virtio.service"
          ];
        };
        environment.systemPackages = [ pkgs.systemd ];
        # The 9p export preserves the host owner's numeric UID. Match it
        # without granting DAC override or exposing the evidence directory.
        users.groups.arena-diagnose = { };
        users.users.arena-diagnose = {
          isSystemUser = true;
          uid = hostUid;
          group = "arena-diagnose";
        };
        systemd.services.arena-fixture = {
          wantedBy = [ "multi-user.target" ];
          serviceConfig = {
            Type = "simple";
            ExecStart = "${pkgs.coreutils}/bin/sleep infinity";
            NoNewPrivileges = true;
            CapabilityBoundingSet = "";
            SystemCallFilter = [ "~bpf" ];
          };
        };
        systemd.services.arena-warden-preflight = {
          wantedBy = [ "multi-user.target" ];
          requires = [
            "out.mount"
            "arena-fixture.service"
          ];
          after = [
            "out.mount"
            "arena-fixture.service"
          ];
          path = [
            pkgs.python314
            pkgs.strace
            pkgs.systemd
          ];
          environment = {
            PYTHONDONTWRITEBYTECODE = "1";
            PYTHONNOUSERSITE = "1";
          };
          serviceConfig = {
            Type = "oneshot";
            User = "arena-diagnose";
            Group = "arena-diagnose";
            StandardOutput = "journal+console";
            StandardError = "journal+console";
            TimeoutStartSec = 90;
            TimeoutStopSec = 10;
            KillMode = "control-group";
            NoNewPrivileges = true;
            # Read PID 1 namespace links and trace children; no CAP_BPF/SYS_ADMIN.
            CapabilityBoundingSet = [ "CAP_SYS_PTRACE" ];
            AmbientCapabilities = [ "CAP_SYS_PTRACE" ];
            RestrictAddressFamilies = [ "AF_UNIX" ];
            SystemCallFilter = [ "~bpf" ];
            LimitFSIZE = "2M";
            UMask = "0077";
          };
          script = ''
            mkdir /out/nonloading
            ${pkgs.python314}/bin/python3 ${../../scripts/warden_preflight.py} guest ${wardenPackage} ${wardenVerifier} /out/nonloading
          '';
          postStop = "${pkgs.python314}/bin/python3 ${../../scripts/warden_preflight_terminal.py} /out";
          unitConfig.OnSuccess = "arena-preflight-poweroff.service";
          unitConfig.OnFailure = "arena-preflight-poweroff.service";
        };
        systemd.services.arena-preflight-poweroff = {
          serviceConfig.Type = "oneshot";
          script = "${pkgs.systemd}/bin/systemctl poweroff";
        };
      })
    ];
  };
  cfg = system.config;
  closure = pkgs.closureInfo { rootPaths = [ cfg.system.build.toplevel ]; };
in
pkgs.runCommand "arena-warden-nonloading-bundle" { } (
  ''
    mkdir -p $out
  ''
  + import (nixpkgs + "/nixos/lib/erofs-store-image.nix") {
    hostPkgs = pkgs;
    storePaths = "${closure}/store-paths";
    label = "nix-store";
    destination = "$out/store.img";
  }
  + ''
    cp ${cfg.system.build.kernel}/${cfg.system.boot.loader.kernelFile} $out/Image
    cp ${cfg.system.build.initialRamdisk}/${cfg.system.boot.loader.initrdFile} $out/initrd
    cp ${cfg.system.build.toplevel}/kernel-params $out/kernel-params
    printf '%s\n' '${cfg.system.build.toplevel}' > $out/toplevel
    printf '%s\n' 'warden-nonloading-v1' > $out/scenario
    printf '%s\n' '${guestSystem}' > $out/guest-system
    cd $out
    sha256sum store.img Image initrd kernel-params toplevel scenario guest-system > SHA256SUMS
  ''
)
