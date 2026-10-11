/* Arena reference, not a production firewall. Fixed IPv4/UDP fixtures only. */
#include <linux/bpf.h>
#include <linux/pkt_cls.h>
#define SEC(name) __attribute__((section(name), used))
#ifndef __always_inline
#define __always_inline inline __attribute__((always_inline))
#endif
struct map_def { __u32 type, key_size, value_size, max_entries, flags, id, pinning; };
struct map_def SEC("maps") MODE = { BPF_MAP_TYPE_ARRAY, 4, 4, 1, 0, 0, 0 };
struct map_def SEC("maps") STATS = { BPF_MAP_TYPE_PERCPU_ARRAY, 4, 8, 6, 0, 0, 0 };
static void *(*lookup)(void *, const void *) = (void *)BPF_FUNC_map_lookup_elem;
static __always_inline void count(__u32 key) {
    __u64 *value = lookup(&STATS, &key);
    if (value) *value += 1;
}
static __always_inline __u16 be16(const unsigned char *p) {
    return ((__u16)p[0] << 8) | p[1];
}
SEC("classifier") int arena_ingress(struct __sk_buff *skb) {
    unsigned char *p = (void *)(unsigned long)skb->data;
    unsigned char *end = (void *)(unsigned long)skb->data_end;
    if (p + 34 > end || be16(p + 12) != 0x0800 || p[14] >> 4 != 4 || p[23] != 17)
        return TC_ACT_OK;
    __u32 ihl = (p[14] & 15) * 4;
    if (ihl < 20 || ihl > 60 || (be16(p + 20) & 0x3fff) != 0)
        return TC_ACT_OK;
    if (p[26] != 10 || p[27] != 77 || p[28] != 1 || p[29] != 10 ||
        p[30] != 10 || p[31] != 77 || p[32] != 2 || p[33] != 10)
        return TC_ACT_OK;
    __u32 total = be16(p + 16);
    if (total < ihl + 12 || p + 14 + total > end) return TC_ACT_OK;
    unsigned char *udp = p + 14 + ihl;
    if (udp + 12 > end || be16(udp) != 39100 || be16(udp + 4) < 12 ||
        udp[8] != 'V' || udp[9] != 'T' || udp[10] != 'C' || udp[11] != '1')
        return TC_ACT_OK;
    __u32 udp_len = be16(udp + 4);
    if (udp_len > total - ihl || udp + udp_len > end) return TC_ACT_OK;
    __u16 port = be16(udp + 2);
    if (port != 39101 && port != 39102) return TC_ACT_OK;
    __u32 key = 0;
    __u32 *mode = lookup(&MODE, &key);
    if (!mode || *mode == 0) return TC_ACT_OK;
    __u32 base = port == 39101 ? 0 : 3;
    count(base);
    if (*mode == 2 && port == 39101) {
        count(base + 2);
        return TC_ACT_SHOT;
    }
    count(base + 1);
    return TC_ACT_OK;
}
char SEC("license") LICENSE[] = "GPL";
