# Separate observe-only guest; never imports the enforcement scenario.
{ nixpkgs, hostUid, wardenInputs, guestSystem ? "aarch64-linux" }:
assert builtins.isInt hostUid && hostUid > 0;
assert guestSystem == "aarch64-linux";
let
  pkgs = import nixpkgs { system = guestSystem; };
  lib = nixpkgs.lib;
  inputs = builtins.path { path = wardenInputs; name = "arena-warden-observe-inputs"; };
  cli = pkgs.stdenv.mkDerivation {
    pname = "arena-warden-observe-cli"; version = "0.1.0";
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
      boot.kernelParams = [ "arena.warden-observe=1" ];
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
      users.groups.arena-evidence.gid = hostUid;
      users.users.arena-evidence = { isSystemUser = true; uid = hostUid; group = "arena-evidence"; };
      systemd.slices.warden = { description = "Arena Warden parent"; };
      systemd.slices.warden-test = { description = "Synthetic observe clients only"; };
      systemd.services.arena-warden-observe = {
        wantedBy = [ "multi-user.target" ];
        after = [ "out.mount" "warden-test.slice" ];
        requires = [ "out.mount" "warden-test.slice" ];
        path = [ pkgs.python314 pkgs.bpftools pkgs.systemd pkgs.coreutils pkgs.util-linux ];
        environment = { PYTHONDONTWRITEBYTECODE = "1"; PYTHONNOUSERSITE = "1"; };
        serviceConfig = { Type = "oneshot"; User = "root"; TimeoutStartSec = 120;
          TimeoutStopSec = 15; KillMode = "control-group"; UMask = "0022";
          StandardOutput = "journal+console"; StandardError = "journal+console"; };
        script = ''
          ${pkgs.python314}/bin/python3 ${../../scripts/warden_observe_guest.py} ${cli}/bin/veil-warden ${inputs}/veil-warden-ebpf /run/warden-observe
        '';
        postStop = ''
          setpriv --reuid=${toString hostUid} --regid=${toString hostUid} --clear-groups \
            ${pkgs.python314}/bin/python3 ${../../scripts}/warden_observe_terminal.py /run/warden-observe /out
        '';
        unitConfig.OnSuccess = "arena-observe-poweroff.service";
        unitConfig.OnFailure = "arena-observe-poweroff.service";
      };
      systemd.services.arena-observe-poweroff = { serviceConfig.Type = "oneshot"; script = "${pkgs.systemd}/bin/systemctl poweroff"; };
    }) ];
  };
  cfg = system.config;
  closure = pkgs.closureInfo { rootPaths = [ cfg.system.build.toplevel ]; };
in pkgs.runCommand "arena-warden-observe-bundle" {} (''
  mkdir -p $out
'' + import (nixpkgs + "/nixos/lib/erofs-store-image.nix") {
  hostPkgs = pkgs; storePaths = "${closure}/store-paths"; label = "nix-store"; destination = "$out/store.img";
} + ''
  cp ${cfg.system.build.kernel}/${cfg.system.boot.loader.kernelFile} $out/Image
  cp ${cfg.system.build.initialRamdisk}/${cfg.system.boot.loader.initrdFile} $out/initrd
  cp ${cfg.system.build.toplevel}/kernel-params $out/kernel-params
  printf '%s\n' '${cfg.system.build.toplevel}' > $out/toplevel
  ${pkgs.python314}/bin/python3 - "$out/inputs.json" <<'PYCODE'
  import hashlib,json,pathlib,sys
  meta=json.loads(pathlib.Path('${inputs}/inputs.json').read_text())
  meta['runtime_cli_sha256']=hashlib.sha256(pathlib.Path('${cli}/bin/veil-warden').read_bytes()).hexdigest()
  pathlib.Path(sys.argv[1]).write_text(json.dumps(meta,indent=2)+'\n')
  PYCODE
  cd $out
  sha256sum store.img Image initrd kernel-params toplevel inputs.json > SHA256SUMS
'')
