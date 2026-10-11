# Evaluated through flake.lib.mkOfflineVm. Guest derivations require a Linux builder.
{ nixpkgs, versions, toolsFor, arenaFor, vmControllerFor, tcReferenceFor, hostSystem, blackholeSource, hostUid, accelerator,
  scenario ? "blackhole-offline", omitServerRoute ? false, guestBundle ? null }:
assert builtins.isInt hostUid && hostUid > 0 && hostUid < 2147483647;
assert builtins.elem accelerator [ "tcg" "kvm" ];
assert accelerator != "kvm" || nixpkgs.lib.hasSuffix "-linux" hostSystem;
let
  lib = nixpkgs.lib;
  batch = scenario == "netns-batch";
  netns = builtins.elem scenario [ "netns-baseline" "netns-batch" ];
  guestTools = if netns then [ pkgs.coreutils pkgs.iproute2 pkgs.iputils pkgs.util-linux pkgs.procps ]
    else toolsFor pkgs ++ [ pkgs.coreutils ];
  guestSystem = if lib.hasPrefix "aarch64-" hostSystem then "aarch64-linux"
    else if lib.hasPrefix "x86_64-" hostSystem then "x86_64-linux"
    else throw "unsupported host system";
  hostPkgs = import nixpkgs { system = hostSystem; };
  pkgs = import nixpkgs { system = guestSystem; };
  bundle = if guestBundle == null then null else builtins.path {
    path = guestBundle; name = "arena-guest-bundle";
  };
  bundleMetadata = if bundle == null then null else
    builtins.fromJSON (builtins.readFile (bundle + "/original-vm-manifest.json"));
  bundleCheck = if bundle == null then null else hostPkgs.runCommand "arena-guest-bundle-check" {} ''
    ${hostPkgs.python314}/bin/python3 ${../../scripts/guest_bundle.py} check ${bundle}
    touch $out
  '';
  arena = arenaFor pkgs;
  tcReference = tcReferenceFor pkgs;
  controller = vmControllerFor hostPkgs;
  # Do not put .git, artifacts, credentials or build caches in the guest closure.
  source = if netns then null else builtins.path {
    path = blackholeSource;
    name = "veil-blackhole-offline-source";
    filter = path: type:
      let relative = lib.removePrefix (toString blackholeSource + "/") path;
          top = builtins.head (lib.splitString "/" relative);
      in type != "symlink" && (builtins.elem top [ "src" "tests" "scripts" "examples" ]
        || builtins.elem relative [ "Cargo.toml" "Cargo.lock" "build.rs" "rust-toolchain.toml" ]);
  };
  vendor = pkgs.rustPlatform.importCargoLock { lockFile = source + "/Cargo.lock"; };
  system = nixpkgs.lib.nixosSystem {
    system = guestSystem;
    modules = [ ({ modulesPath, ... }: {
      imports = [ (modulesPath + "/virtualisation/qemu-vm.nix") ];
      system.stateVersion = "26.05";
      networking.hostName = "veil-arena-offline";
      boot.kernelPackages = pkgs.linuxPackages_6_12;
      boot.kernelModules = lib.optionals netns [ "veth" "sch_ingress" "cls_bpf" ];
      boot.kernelParams = lib.optionals netns [ "arena.netns=1" ];
      assertions = [{ assertion = pkgs.linuxPackages_6_12.kernel.version == versions.kernel;
        message = "guest kernel version drift"; }];
      boot.initrd.availableKernelModules = [ "virtio_blk" "virtio_pci" "9p" "9pnet_virtio" "erofs" ];
      virtualisation = {
        diskImage = null;
        graphics = false;
        memorySize = 4096;
        cores = 2;
        useNixStoreImage = true;
        mountHostNixStore = false;
        writableStore = false;
        useHostCerts = false;
        host.pkgs = hostPkgs;
        sharedDirectories = lib.mkForce {
          out = { source = "$ARENA_VM_OUT"; target = "/out"; writable = true; };
        };
        qemu.networkingOptions = lib.mkForce [ "-nic none" ];
      };
      # The pinned module uses virtiofs on Linux hosts. Our launcher deliberately
      # uses the same unprivileged 9P transport on every supported host.
      virtualisation.fileSystems."/out" = {
        fsType = lib.mkForce "9p";
        options = lib.mkForce [ "trans=virtio" "version=9p2000.L" "msize=1048576"
          "x-systemd.requires=modprobe@9pnet_virtio.service" "rw" ];
      };
      networking.useDHCP = false;
      services.openssh.enable = false;
      nix.enable = false;
      # Match the host output owner so a private 0700 9P share remains usable.
      users.users.arena = { isSystemUser = true; group = "users"; uid = hostUid; };
      environment.systemPackages = guestTools ++ [ arena ];
      systemd.services.arena-offline = {
        wantedBy = [ "multi-user.target" ];
        after = [ "out.mount" ];
        requires = [ "out.mount" ];
        path = guestTools;
        serviceConfig = {
          User = if netns then "root" else "arena";
          WorkingDirectory = "/work";
          RuntimeMaxSec = 600;
          TimeoutStopSec = 5;
          PrivateNetwork = true;
          NoNewPrivileges = true;
        };
        preStart = if netns then "" else ''
          mkdir -p /work/blackhole /work/cargo
          cp -R ${source}/. /work/blackhole/
          chmod -R u+w /work/blackhole
          cd /work/blackhole
          git init -q
          git -c user.name=arena-fixture -c user.email=arena@invalid add .
          git -c user.name=arena-fixture -c user.email=arena@invalid commit -qm 'offline source snapshot'
          cat > /work/cargo/config.toml <<EOF
          [source.crates-io]
          replace-with = "vendored-sources"
          [source.vendored-sources]
          directory = "${vendor}"
          [net]
          offline = true
          EOF
        '';
        script = ''
          export CARGO_HOME=/work/cargo PYTHONNOUSERSITE=1
          ${lib.optionalString batch ''export ARENA_TC_OBJECT=${tcReference}/tc_ingress.o
            export ARENA_TC_REJECT_OBJECT=${tcReference}/tc_load_reject.o''}
          uname -a > /out/guest-kernel.txt
          cat /proc/cmdline > /out/guest-cmdline.txt
          ${lib.optionalString batch ''cat /proc/sys/kernel/random/boot_id > /out/guest-boot-id.txt''}
          ${if batch then ''
            case " $(cat /proc/cmdline) " in
              *" arena.watchdog=1 "*) ${arena}/bin/veil-arena-netns --batch-watchdog /out/netns-batch ;;
              *) ${arena}/bin/veil-arena-netns --batch /out/netns-batch ;;
            esac
          '' else if netns then ''
            ${arena}/bin/veil-arena-netns --artifacts /out/netns-results
          '' else ''
            printf '%s\n' '${source}' > /out/source-store-path.txt
            ${arena}/bin/veil-arena blackhole-offline --project /work/blackhole \
              --artifacts /out/results --timeout-seconds 300
          ''}
        '';
        # Preserve failure too, then shut down this guest. No host resources are deleted.
        postStop = ''
          printf '%s\n' "$SERVICE_RESULT" > /out/service-result.txt
        '';
        unitConfig.OnSuccess = "arena-poweroff.service";
        unitConfig.OnFailure = "arena-poweroff.service";
      };
      systemd.tmpfiles.rules = [ "d /work 0700 arena users -" ];
      systemd.services.arena-poweroff = {
        serviceConfig.Type = "oneshot";
        script = "${pkgs.systemd}/bin/systemctl poweroff";
      };
    }) ];
  };
  cfg = system.config;
  closure = pkgs.closureInfo { rootPaths = [ cfg.system.build.toplevel ]; };
  image = if bundle != null then bundle else pkgs.runCommand "arena-${if netns then "netns" else "offline"}-store-image" {} (''
    mkdir -p $out
  '' + import (nixpkgs + "/nixos/lib/erofs-store-image.nix") {
    hostPkgs = pkgs;
    storePaths = "${closure}/store-paths";
    label = "nix-store";
    destination = "$out/store.img";
  });
  guestToplevel = if bundle != null then bundleMetadata.guest_system else toString cfg.system.build.toplevel;
  kernelFile = if bundle != null then "${bundle}/Image" else "${cfg.system.build.kernel}/${cfg.system.boot.loader.kernelFile}";
  initrdFile = if bundle != null then "${bundle}/initrd" else "${cfg.system.build.initialRamdisk}/${cfg.system.boot.loader.initrdFile}";
  paramsFile = if bundle != null then "${bundle}/kernel-params" else "${cfg.system.build.toplevel}/kernel-params";
  metadata = hostPkgs.writeText "vm-manifest.json" (builtins.toJSON {
    schema_version = 1;
    inherit hostSystem guestSystem versions hostUid;
    inherit scenario omitServerRoute;
    nixpkgs_revision = nixpkgs.rev or "8edc0c72e3a38faf5434e40d1f19431125fe4b30";
    source_store_path = if netns then null else toString source;
    arena_package = if bundle != null then bundleMetadata.arena_package else toString arena;
    tc_reference = if bundle != null then bundleMetadata.tc_reference else if batch then toString tcReference else null;
    aya_version = if batch then "0.13.1" else null;
    guest_system = guestToplevel;
    store_image = toString image;
    network = "no NIC";
    inherit accelerator;
    memory_mib = 4096;
    vcpus = 2;
    host_mounts = [ "dedicated output directory only" ];
    output_transport = "9p2000.L";
    lifecycle = "host fsync available evidence before owned QEMU stop; completion or watchdog";
    watchdog_fixture_supported = batch;
    host_controller = toString controller;
  });
  console = if guestSystem == "aarch64-linux" then "ttyAMA0" else "ttyS0";
  launcher = hostPkgs.writeShellApplication {
    name = "arena-vm-${if batch then "netns-batch" else if netns then "netns" else "offline"}";
    runtimeInputs = [ hostPkgs.coreutils hostPkgs.qemu hostPkgs.python314 ];
    text = ''
      ${lib.optionalString (bundle != null) ''test -f ${bundleCheck}''}
      if [ "''${1:-}" = "--describe" ] && [ "$#" -eq 1 ]; then cat ${metadata}; exit 0; fi
      fixture=false
      fixture_param=""
      controller_args=()
      ${lib.optionalString batch ''if [ "''${1:-}" = "--watchdog-fixture" ]; then
        fixture=true
        fixture_param=" arena.watchdog=1"
        controller_args=(--watchdog-fixture)
        shift
      fi''}
      if [ "$#" -ne 1 ]; then echo "usage: arena-vm-offline NEW_OUTPUT_DIRECTORY" >&2; exit 2; fi
      if [ "$(id -u)" -ne ${toString hostUid} ]; then echo "host UID differs from VM definition" >&2; exit 3; fi
      case "$1" in *','*|*$'\n'*) echo "output path contains an unsupported character" >&2; exit 2;; esac
      umask 077
      # Reject existing paths. Keep output on failures and cancellations.
      if [ -e "$1" ] || [ -L "$1" ]; then echo "output path already exists" >&2; exit 2; fi
      if ! mkdir -- "$1"; then echo "cannot create output directory" >&2; exit 3; fi
      ARENA_VM_OUT=$(realpath -- "$1")
      export ARENA_VM_OUT
      case "$ARENA_VM_OUT" in *','*|*$'\n'*) echo "resolved output path contains an unsupported character" >&2; exit 2;; esac
      if ! cp ${metadata} "$ARENA_VM_OUT/vm-manifest.json"; then exit 3; fi
      if ! sha256sum ${image}/store.img \
        ${kernelFile} \
        ${initrdFile} \
        > "$ARENA_VM_OUT/boot-inputs.sha256"; then exit 3; fi
      set +e
      (ulimit -f 65536; exec timeout --signal=TERM --kill-after=5 670 \
        ${controller}/bin/veil-arena-vm-controller "''${controller_args[@]}" "$ARENA_VM_OUT" \
        qemu-system-${if guestSystem == "aarch64-linux" then "aarch64" else "x86_64"} \
        -machine ${if guestSystem == "aarch64-linux" then "virt" else "q35"} \
        -cpu ${if accelerator == "kvm" then "host" else "max"} \
        -accel ${accelerator} -m 4096 -smp 2 -nographic -monitor none -no-reboot -nic none \
        -kernel ${kernelFile} \
        -initrd ${initrdFile} \
        -append "$(cat ${paramsFile}) init=${guestToplevel}/init console=${console}${lib.optionalString (netns && omitServerRoute) " arena.omit_server_route=1"}$fixture_param" \
        -drive file=${image}/store.img,if=virtio,format=raw,readonly=on \
        -virtfs "local,path=$ARENA_VM_OUT,security_model=none,mount_tag=out") &
      watchdog=$!
      trap 'kill -TERM "$watchdog" 2>/dev/null; wait "$watchdog"; exit 3' INT TERM HUP
      wait "$watchdog"
      code=$?
      trap - INT TERM HUP
      set -e
      if [ "$fixture" = true ]; then
        if [ "$code" -ne 3 ]; then echo "watchdog fixture returned unexpected lifecycle status" >&2; exit 3; fi
        python3 ${../../scripts/check_vm_result.py} "$ARENA_VM_OUT" netns-watchdog
        echo "Expected watchdog environment error verified: $ARENA_VM_OUT"
        exit 3
      fi
      if [ "$code" -ne 0 ]; then echo "VM failed or timed out: $ARENA_VM_OUT" >&2; exit 3; fi
      python3 ${../../scripts/check_vm_result.py} "$ARENA_VM_OUT" ${lib.optionalString netns scenario}
    '';
  };
  workflow = hostPkgs.writeShellApplication {
    name = "arena-netns";
    text = ''exec ${controller}/bin/veil-arena-netns-workflow ${launcher}/bin/arena-vm-netns-batch "$@"'';
  };
in assert cfg.fileSystems."/out".fsType == "9p";
  assert bundle == null || (batch && accelerator == "tcg"
    && bundleMetadata.guestSystem == guestSystem && bundleMetadata.hostUid == hostUid
    && bundleMetadata.versions == versions
    && builtins.match "/nix/store/[0-9a-z]+-nixos-system-[0-9A-Za-z._+-]+" guestToplevel != null);
  if batch then hostPkgs.symlinkJoin { name = "arena-netns-prepared"; paths = [ launcher workflow ]; }
  else launcher
