//! Only a root qdisc on our owned router namespace, downstream of TC ingress.
use super::{Run, save};
use serde_json::{Value, json};
use std::{io, path::Path};

pub(super) fn owned(value: &Value) -> Option<&Value> {
    let rows = value.as_array()?;
    if rows.len() != 1 {
        return None;
    }
    let row = &rows[0];
    (row["kind"] == "netem" && row["handle"] == "7a00:" && row["root"] == true).then_some(row)
}

pub(super) fn absent(value: &Value) -> bool {
    value.as_array().is_some_and(|rows| {
        rows.iter()
            .all(|r| r["kind"] == "noqueue" && r["handle"] == "0:" && r["root"] == true)
    })
}

pub(super) fn configured(value: &Value) -> bool {
    owned(value).is_some_and(|r| r["options"]["loss-random"]["loss"].as_f64() == Some(1.0))
}

pub(super) fn drops(value: &Value) -> Option<u64> {
    owned(value)?["drops"].as_u64()
}

pub(super) fn snapshot(run: &mut Run, router: u32, label: &str) -> io::Result<Value> {
    serde_json::from_slice(&run.command(
        label,
        Some(router),
        &["tc", "-j", "-s", "qdisc", "show", "dev", "rt-server"],
        0,
    )?)
    .map_err(io::Error::other)
}

fn mac_valid(value: &str) -> bool {
    let parts: Vec<_> = value.split(':').collect();
    parts.len() == 6
        && parts
            .iter()
            .all(|p| p.len() == 2 && u8::from_str_radix(p, 16).is_ok())
}

pub(super) fn fixture_ready(value: &Value) -> bool {
    let neighbor = |rows: &Value, address: &str, dev: &str, mac: &Value| {
        rows.as_array().is_some_and(|rows| {
            rows.len() == 1
                && rows[0]["dst"] == address
                && rows[0]["dev"] == dev
                && rows[0]["lladdr"] == *mac
                && rows[0]["state"] == json!(["PERMANENT"])
        })
    };
    value["router_ipv6_disabled"] == true
        && value["router_mac"].as_str().is_some_and(mac_valid)
        && value["server_mac"].as_str().is_some_and(mac_valid)
        && neighbor(
            &value["router_neighbor"],
            "10.77.2.10",
            "rt-server",
            &value["server_mac"],
        )
        && neighbor(
            &value["server_neighbor"],
            "10.77.2.1",
            "server0",
            &value["router_mac"],
        )
}

fn fixture_control(run: &mut Run, router: u32, server: u32) -> io::Result<Value> {
    let router_link: Value = serde_json::from_slice(&run.command(
        "netem-router-link",
        Some(router),
        &["ip", "-j", "link", "show", "dev", "rt-server"],
        0,
    )?)?;
    let server_link: Value = serde_json::from_slice(&run.command(
        "netem-server-link",
        Some(server),
        &["ip", "-j", "link", "show", "dev", "server0"],
        0,
    )?)?;
    let router_mac = router_link[0]["address"]
        .as_str()
        .filter(|m| mac_valid(m))
        .ok_or_else(|| io::Error::other("router MAC missing"))?;
    let server_mac = server_link[0]["address"]
        .as_str()
        .filter(|m| mac_valid(m))
        .ok_or_else(|| io::Error::other("server MAC missing"))?;
    run.command(
        "netem-router-ipv6-off",
        Some(router),
        &["sysctl", "-w", "net.ipv6.conf.rt-server.disable_ipv6=1"],
        0,
    )?;
    let ipv6 = run.command(
        "netem-router-ipv6-readback",
        Some(router),
        &["sysctl", "-n", "net.ipv6.conf.rt-server.disable_ipv6"],
        0,
    )?;
    run.command(
        "netem-router-neighbor-static",
        Some(router),
        &[
            "ip",
            "neigh",
            "replace",
            "10.77.2.10",
            "lladdr",
            server_mac,
            "dev",
            "rt-server",
            "nud",
            "permanent",
        ],
        0,
    )?;
    run.command(
        "netem-server-neighbor-static",
        Some(server),
        &[
            "ip",
            "neigh",
            "replace",
            "10.77.2.1",
            "lladdr",
            router_mac,
            "dev",
            "server0",
            "nud",
            "permanent",
        ],
        0,
    )?;
    let router_neighbor: Value = serde_json::from_slice(&run.command(
        "netem-router-neighbor-readback",
        Some(router),
        &["ip", "-j", "neigh", "show", "to", "10.77.2.10"],
        0,
    )?)?;
    let server_neighbor: Value = serde_json::from_slice(&run.command(
        "netem-server-neighbor-readback",
        Some(server),
        &["ip", "-j", "neigh", "show", "to", "10.77.2.1"],
        0,
    )?)?;
    let value = json!({"router_mac":router_mac,"server_mac":server_mac,"router_ipv6_disabled":ipv6 == b"1\n",
        "router_neighbor":router_neighbor,"server_neighbor":server_neighbor});
    if !fixture_ready(&value) {
        return Err(io::Error::other("NetEm fixture control differs"));
    }
    Ok(value)
}

#[derive(Default)]
pub(super) struct State {
    pub attempted: bool,
    pub evidence: Value,
}

impl State {
    pub fn setup(
        &mut self,
        run: &mut Run,
        router: u32,
        server: u32,
        out: &Path,
        fault: &str,
    ) -> io::Result<()> {
        let before = snapshot(run, router, "netem-before")?;
        if !absent(&before) {
            return Err(io::Error::other("unexpected existing router qdisc"));
        }
        let fixture = fixture_control(run, router, server)?;
        self.attempted = true;
        run.command(
            "netem-install",
            Some(router),
            &[
                "tc",
                "qdisc",
                "add",
                "dev",
                "rt-server",
                "root",
                "handle",
                "7a00:",
                "netem",
                "loss",
                if fault == "netem-unconfigured" {
                    "0%"
                } else {
                    "100%"
                },
            ],
            0,
        )?;
        let actual = snapshot(run, router, "netem-configured")?;
        self.evidence = json!({"interface":"rt-server","direction":"egress","position":"after-router-tc-ingress",
            "fixture_control":fixture,"before":before,"configured":actual,"expected_loss":1.0,"sender_started":false,
            "configuration_verified":configured(&actual)});
        save(&out.join("netem-setup.json"), &self.evidence)?;
        if !configured(&actual) {
            return Err(io::Error::other("NetEm loss configuration differs"));
        }
        Ok(())
    }

    pub fn remove(&mut self, run: &mut Run, router: u32, out: &Path) -> io::Result<()> {
        if !self.attempted {
            return Ok(());
        }
        let before = snapshot(run, router, "netem-cleanup-before")?;
        // Never delete an unrecognized qdisc, even in our namespace.
        if owned(&before).is_none() {
            return Err(io::Error::other(
                "owned NetEm identity missing before cleanup",
            ));
        }
        run.command(
            "netem-remove",
            Some(router),
            &[
                "tc",
                "qdisc",
                "del",
                "dev",
                "rt-server",
                "root",
                "handle",
                "7a00:",
            ],
            0,
        )?;
        let after = snapshot(run, router, "netem-cleanup-after")?;
        let clean = absent(&after);
        self.evidence["cleanup"] = json!({"before":before,"after":after,"removed":clean});
        save(&out.join("netem-cleanup.json"), &self.evidence["cleanup"])?;
        if !clean {
            return Err(io::Error::other("NetEm survived removal"));
        }
        self.attempted = false;
        Ok(())
    }
}

pub(super) fn clean(r: &Value) -> bool {
    owned(&r["cleanup"]["before"]).is_some()
        && r["cleanup"]["removed"] == true
        && absent(&r["cleanup"]["after"])
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn fixture_control_requires_both_permanent_neighbors_and_ipv6_off() {
        let mut value = json!({"router_ipv6_disabled":true,"router_mac":"02:00:00:00:00:01","server_mac":"02:00:00:00:00:02",
            "router_neighbor":[{"dst":"10.77.2.10","dev":"rt-server","lladdr":"02:00:00:00:00:02","state":["PERMANENT"]}],
            "server_neighbor":[{"dst":"10.77.2.1","dev":"server0","lladdr":"02:00:00:00:00:01","state":["PERMANENT"]}]});
        assert!(fixture_ready(&value));
        value["server_neighbor"][0]["state"] = json!(["STALE"]);
        assert!(!fixture_ready(&value));
        value["server_neighbor"][0]["state"] = json!(["PERMANENT"]);
        value["router_ipv6_disabled"] = json!(false);
        assert!(!fixture_ready(&value));
    }

    #[test]
    fn loss_requires_readback_and_owned_identity() {
        let mut value = json!([{"kind":"netem","root":true,"handle":"7a00:","options":{"loss-random":{"loss":1.0}},"drops":8}]);
        assert!(configured(&value));
        assert_eq!(drops(&value), Some(8));
        value[0]["options"]["loss-random"]["loss"] = json!(0.0);
        assert!(!configured(&value));
        value[0]["handle"] = json!("7b00:");
        assert!(owned(&value).is_none());
        assert!(!absent(&value));
        assert!(!absent(&Value::Null));
    }
}
