/* Valid ELF; intentionally rejected by the kernel verifier. Never attach. */
#include <linux/bpf.h>
#define SEC(name) __attribute__((section(name), used))
struct map_def { __u32 type, key_size, value_size, max_entries, flags, id, pinning; };
struct map_def SEC("maps") MODE = { BPF_MAP_TYPE_ARRAY, 4, 4, 1, 0, 0, 0 };
struct map_def SEC("maps") STATS = { BPF_MAP_TYPE_PERCPU_ARRAY, 4, 8, 6, 0, 0, 0 };
static long (*rejected_helper)(void) = (void *)999999;
SEC("classifier") int arena_rejected(struct __sk_buff *skb) {
    (void)skb;
    return rejected_helper();
}
char SEC("license") LICENSE[] = "GPL";
