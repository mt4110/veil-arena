# Dedicated F1 guest; no host NIC, host store, credentials or Keeper profile.
{ nixpkgs, hostUid, f2Role ? null, keeperSource ? null }:
assert builtins.isInt hostUid && hostUid > 0;
let
  lib = nixpkgs.lib;
  system = builtins.currentSystem;
  pkgs = import nixpkgs { inherit system; };
  contract = builtins.fromJSON (builtins.readFile ../../docs/contracts/f0-inputs.json);
  f2 = f2Role != null;
  f3 = keeperSource != null;
  keeperSnapshot = pkgs.runCommand "arena-keeper-fixed-inputs" {} ''
    mkdir -p $out
    cp ${keeperSource}/m0_exec_check.py $out/m0_exec_check.py
    cp ${keeperSource}/m0-exec-check.profile $out/m0-exec-check.profile
  '';
  role = if f2 then f2Role else "server";
  ip = if role == "server" then "10.77.10.1" else "10.77.10.2";
  limits = if f2 then (builtins.removeAttrs contract.f2_limits [ "server" "agent" ]) // contract.f2_limits.${role} else contract.f1_limits;
  guestScript = if f3 then ../../scripts/k3s_f3_guest.py else if f2 then ../../scripts/k3s_f2_guest.py else ../../scripts/k3s_guest.py;
  guestModules = pkgs.runCommand "arena-k3s-f2-guest-modules" {} (''
    mkdir -p $out
    cp ${../../scripts/k3s_guest.py} $out/k3s_guest.py
    cp ${../../scripts/k3s_f2_guest.py} $out/k3s_f2_guest.py
  '' + lib.optionalString f3 ''
    cp ${../../scripts/k3s_f3_guest.py} $out/k3s_f3_guest.py
    cp ${../../scripts/keeper_trial.py} $out/keeper_trial.py
    cp ${../../scripts/k3s_checkpoint.py} $out/k3s_checkpoint.py
    cp ${../../scripts/k3s_bundle.py} $out/k3s_bundle.py
  '');
  fixtureScript = if f2 then ../../tests/k3s/f2_fixture.py else ../../tests/k3s/fixture.py;
  fixture = pkgs.writeTextDir "bin/arena-k3s-fixture.py" (builtins.readFile fixtureScript);
  fixtureCommand = [ "${pkgs.python314}/bin/python3" "${fixture}/bin/arena-k3s-fixture.py" ];
  imageTag = "${if f2 then "f2" else "f1"}-${builtins.substring 0 16 (builtins.hashString "sha256" (builtins.readFile fixtureScript))}";
  imageName = "docker.io/veil-arena/fixture:${imageTag}";
  fixtureImage = pkgs.dockerTools.buildLayeredImage {
    name = "veil-arena/fixture";
    tag = imageTag;
    maxLayers = 2;
    contents = [ pkgs.python314 fixture ];
    config = { Cmd = fixtureCommand; Env = [ "PYTHONUNBUFFERED=1" ]; };
  };
  identity = pkgs.runCommand "arena-fixture-identity.json" { nativeBuildInputs = [ pkgs.python314 ]; } ''
    python3 - ${fixtureImage} > $out <<'PYCODE'
    import hashlib, json, sys, tarfile
    with tarfile.open(sys.argv[1]) as image:
        manifest = json.load(image.extractfile("manifest.json"))[0]
        config = image.extractfile(manifest["Config"]).read()
    print(json.dumps({"config_digest": "sha256:" + hashlib.sha256(config).hexdigest()}))
    PYCODE
  '';
  manifest = pkgs.writeText "arena-k3s-fixtures.json" (builtins.toJSON {
    apiVersion = "v1"; kind = "List";
    items = [
      { apiVersion = "v1"; kind = "Namespace"; metadata.name = if f2 then "arena-f2" else "arena-f1"; }
      { apiVersion = "v1"; kind = "Service";
        metadata = { name = "echo"; namespace = if f2 then "arena-f2" else "arena-f1"; };
        spec = { selector.app = "echo"; ports = [
          { name = "tcp"; port = 18080; targetPort = 18080; protocol = "TCP"; }
          { name = "udp"; port = 18080; targetPort = 18080; protocol = "UDP"; }
        ]; }; }
    ] ++ map (role: {
      apiVersion = "v1"; kind = "Pod";
      metadata = { name = role; namespace = if f2 then "arena-f2" else "arena-f1"; labels.app = if role == "server" then "echo" else role; };
      spec = lib.optionalAttrs f2 { nodeName = "arena-k3s-${if role == "server" then "server" else "agent"}"; } // { restartPolicy = "Never"; automountServiceAccountToken = false;
        containers = [({ name = role; image = imageName; imagePullPolicy = "Never";
          command = fixtureCommand ++ [ (if role == "server" then "server" else "idle") ];
          resources = { requests = { cpu = "10m"; memory = "32Mi"; }; limits = { cpu = "500m"; memory = "128Mi"; }; };
        } // lib.optionalAttrs (role == "server") {
          readinessProbe = { tcpSocket.port = 18080; periodSeconds = 1; timeoutSeconds = 1; };
        })];
      };
    }) ([ "server" "client" ] ++ lib.optional f2 "control");
  });
  compatElf = lib.genAttrs [ "true" "false" ] (name: {
    source = "${pkgs.coreutils}/bin/${name}";
    destination = "/usr/bin/${name}";
    sha256 = builtins.hashFile "sha256" "${pkgs.coreutils}/bin/${name}";
  });
  inputs = pkgs.writeText "k3s-inputs.json" (builtins.toJSON ({
    schema_version = 1; inherit limits hostUid system imageName;
    scenario = if f3 then "k3s-f3" else if f2 then "k3s-f2" else "k3s-f1";
    inherit role;
    versions = contract.versions;
    fixture_command = fixtureCommand;
    compat_elf = compatElf;
    keeper_child_uid = contract.keeper.child_uid;
    keeper_child_gid = contract.keeper.child_gid;
    compat_python = "${pkgs.python314}/bin/python3";
    compat_aa_exec = "${pkgs.apparmor-bin-utils}/bin/aa-exec";
    apparmor_parser = "${pkgs.apparmor-parser}/bin/apparmor_parser";
    fixture_config_digest = (builtins.fromJSON (builtins.readFile identity)).config_digest;
    nixpkgs_revision = contract.nixpkgs.revision;
    airgap_sha256 = contract.airgap_archives.${system}.sha256;
    fixture_script_sha256 = builtins.hashString "sha256" (builtins.readFile fixtureScript);
    common_guest_sha256 = builtins.hashString "sha256" (builtins.readFile ../../scripts/k3s_guest.py);
    guest_runner_sha256 = builtins.hashString "sha256" (builtins.readFile guestScript);
    manifest_sha256 = builtins.hashString "sha256" (builtins.readFile manifest);
  } // lib.optionalAttrs f3 { lifecycle_checkpoint_sha256 = builtins.hashString "sha256" (builtins.readFile ../../scripts/k3s_checkpoint.py); keeper = {
    inherit (contract.keeper) input_sha256 profile_name child_uid child_gid;
    command_timeout_seconds = contract.keeper.apparmor_lifecycle.command_timeout_seconds;
  }; }));
  guest = lib.nixosSystem {
    inherit system;
    modules = [ ({ modulesPath, ... }: {
      imports = [ (modulesPath + "/virtualisation/qemu-vm.nix") ];
      system.stateVersion = "26.05";
      networking.hostName = "arena-k3s-${role}";
      networking.usePredictableInterfaceNames = false;
      networking.useDHCP = false;
      networking.firewall.enable = false; # Dedicated VM without a NIC; CNI owns guest rules.
      services.openssh.enable = false;
      services.resolved.enable = false;
      nix.enable = false;
      boot.kernelPackages = pkgs.linuxPackages_6_12;
      boot.kernelModules = [ "dummy" "br_netfilter" "overlay" "vxlan" ];
      boot.initrd.availableKernelModules = [ "virtio_blk" "virtio_pci" "9p" "9pnet_virtio" "erofs" "ext4" ];
      security.apparmor.enable = true;
      boot.kernel.sysctl = { "net.ipv4.ip_forward" = 1; "net.bridge.bridge-nf-call-iptables" = 1; };
      virtualisation = {
        diskImage = null; graphics = false;
        memorySize = limits.memory_mib; cores = limits.vcpus;
        useNixStoreImage = true; mountHostNixStore = false; writableStore = false;
        useHostCerts = false;
        sharedDirectories = lib.mkForce { out = { source = "$ARENA_VM_OUT"; target = "/out"; writable = true; }; };
        qemu.networkingOptions = lib.mkForce [ "-nic none" ];
      };
      virtualisation.fileSystems."/out" = { fsType = lib.mkForce "9p";
        options = lib.mkForce [ "trans=virtio" "version=9p2000.L" "msize=1048576" "x-systemd.requires=modprobe@9pnet_virtio.service" "rw" ]; };
      virtualisation.fileSystems."/control" = lib.mkIf f2 { device = "control"; fsType = "9p";
        options = [ "trans=virtio" "version=9p2000.L" "msize=1048576" "ro" "x-systemd.requires=modprobe@9pnet_virtio.service" ]; };
      virtualisation.fileSystems."/var" = { device = "/dev/disk/by-id/virtio-arena-run-state"; fsType = "ext4"; autoFormat = true; };
      services.k3s = {
        enable = true; package = pkgs.k3s; inherit role;
        serverAddr = lib.mkIf (f2 && role == "agent") "https://10.77.10.1:6443";
        tokenFile = lib.mkIf f2 "/control/join-token";
        disable = lib.optionals (role == "server") [ "traefik" "servicelb" "local-storage" "metrics-server" ];
        images = [ pkgs.k3s.airgap-images fixtureImage ];
        extraFlags = [ "--node-ip=${ip}" "--flannel-iface=${if f2 then "eth0" else "arena0"}"
          "--resolv-conf=/etc/arena-resolv.conf" ] ++ lib.optionals (role == "server") [
          "--advertise-address=${ip}" "--flannel-backend=vxlan" "--cluster-cidr=10.42.0.0/16"
          "--service-cidr=10.43.0.0/16" ];
      };
      environment.etc."arena-resolv.conf".text = "nameserver 10.77.10.254\n";
      systemd.services.k3s = { wantedBy = lib.mkForce [ ]; after = [ "arena-link.service" ] ++ lib.optional f2 "control.mount"; requires = [ "arena-link.service" ] ++ lib.optional f2 "control.mount"; serviceConfig.Restart = lib.mkForce "no"; };
      systemd.services.arena-link = {
        serviceConfig = { Type = "oneshot"; RemainAfterExit = true; };
        path = [ pkgs.iproute2 ];
        script = if f2 then ''
          ip addr add ${ip}/24 dev eth0
          ip link set eth0 up
          ip route add default via 10.77.10.254 dev eth0 metric 1000
        '' else ''
          ip link add arena0 type dummy
          ip addr add 10.77.10.1/24 dev arena0
          ip link set arena0 up
          ip route add default via 10.77.10.254 dev arena0 metric 1000
        '';
      };
      systemd.services.arena-k3s = {
        wantedBy = [ "multi-user.target" ];
        after = [ "out.mount" "var.mount" "apparmor.service" ] ++ lib.optional f2 "control.mount";
        requires = [ "out.mount" "var.mount" ] ++ lib.optional f2 "control.mount";
        path = [ pkgs.k3s pkgs.systemd pkgs.coreutils pkgs.iptables ];
        preStart = ''
          ${lib.optionalString f3 "test ! -e /inputs; ln -s ${keeperSnapshot} /inputs"}
          mkdir -p /usr/bin
          test ! -e /usr/bin/true
          test ! -e /usr/bin/false
          install -m 0555 ${pkgs.coreutils}/bin/true /usr/bin/true
          install -m 0555 ${pkgs.coreutils}/bin/false /usr/bin/false
          test ! -e /usr/bin/python3
          test ! -e /usr/bin/aa-exec
          ln -s ${pkgs.python314}/bin/python3 /usr/bin/python3
          ln -s ${pkgs.apparmor-bin-utils}/bin/aa-exec /usr/bin/aa-exec
        '';
        serviceConfig = { Type = "oneshot"; TimeoutStartSec = limits.workflow_seconds; TimeoutStopSec = 5; };
        script = ''exec ${pkgs.python314}/bin/python3 ${if f3 then "${guestModules}/k3s_f3_guest.py" else if f2 then "${guestModules}/k3s_f2_guest.py" else toString guestScript} ${inputs} ${manifest}'';
        postStop = ''printf '%s\n' "$SERVICE_RESULT" > /out/service-result.txt'';
        unitConfig = { OnSuccess = "arena-poweroff.service"; OnFailure = "arena-poweroff.service"; };
      };
      systemd.services.arena-poweroff = { serviceConfig.Type = "oneshot"; script = "${pkgs.systemd}/bin/systemctl poweroff"; };
    }) ];
  };
  cfg = guest.config;
  closure = pkgs.closureInfo { rootPaths = [ cfg.system.build.toplevel ]; };
  image = pkgs.runCommand "arena-k3s-store-image" {} (''
    mkdir -p $out
  '' + import (nixpkgs + "/nixos/lib/erofs-store-image.nix") {
    hostPkgs = pkgs; storePaths = "${closure}/store-paths"; label = "nix-store"; destination = "$out/store.img";
  });
in assert !(f2 && f3);
   assert !f3 || builtins.all (name:
     builtins.hashFile "sha256" (keeperSource + "/${builtins.baseNameOf name}") == contract.keeper.input_sha256.${name}
   ) (builtins.attrNames contract.keeper.input_sha256);
   assert f2Role == null || builtins.elem f2Role [ "server" "agent" ];
   assert builtins.elem system [ "aarch64-linux" "x86_64-linux" ];
   assert pkgs.k3s.version == contract.versions.k3s;
   assert pkgs.python314.version == contract.versions.python;
   assert pkgs.coreutils.version == contract.versions.coreutils;
   assert pkgs.apparmor-parser.version == contract.versions.apparmor;
   assert cfg.boot.kernelPackages.kernel.version == contract.versions.kernel;
pkgs.runCommand "arena-k3s-${if f3 then "f3-keeper" else if f2 then "f2-${role}" else "guest"}-bundle" { nativeBuildInputs = [ pkgs.python314 pkgs.qemu pkgs.zstd ]; } ''
  mkdir -p $out
  cp ${image}/store.img $out/store.img
  cp ${cfg.system.build.kernel}/${cfg.system.boot.loader.kernelFile} $out/Image
  cp ${cfg.system.build.initialRamdisk}/${cfg.system.boot.loader.initrdFile} $out/initrd
  cp ${cfg.system.build.toplevel}/kernel-params $out/kernel-params
  cp ${inputs} $out/inputs.json
  cp ${manifest} $out/fixtures.json
  echo '${cfg.system.build.toplevel}' > $out/toplevel
  qemu-img create -f qcow2 $out/run-base.qcow2 ${toString limits.writable_run_disk_virtual_gib}G
  python3 ${../../scripts/k3s_bundle.py} seal $out ${pkgs.k3s.airgap-images} ${fixtureImage}
''
