"""Explicit host execution routes; never fall back from KVM to emulation."""
import os
import platform


def route():
    host = (platform.system(), platform.machine())
    if host[0] == "Darwin" and host[1] in {"arm64", "aarch64"}:
        return {"arch": "aarch64", "accelerator": "tcg", "cpu": "max"}
    if host == ("Linux", "x86_64"):
        return {"arch": "x86_64", "accelerator": "kvm", "cpu": "host"}
    raise ValueError(f"unsupported runtime host: {host[0]}/{host[1]}")


def execution():
    selected = route()
    if selected["accelerator"] == "kvm":
        import fcntl
        # KVM_GET_API_VERSION / KVM_CREATE_VM: Linux UAPI linux/kvm.h.
        # The empty VM fd is closed here; no vCPU, memory or network is created.
        fd = os.open("/dev/kvm", os.O_RDWR | os.O_CLOEXEC)
        try:
            if fcntl.ioctl(fd, 0xAE00, 0) != 12:
                raise ValueError("unsupported KVM API version")
            vm = fcntl.ioctl(fd, 0xAE01, 0)
            os.close(vm)
        finally:
            os.close(fd)
    return selected
