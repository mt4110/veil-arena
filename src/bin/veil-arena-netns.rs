#[cfg(target_os = "linux")]
#[path = "../netns.rs"]
mod netns;

fn main() {
    #[cfg(target_os = "linux")]
    std::process::exit(netns::main());
    #[cfg(not(target_os = "linux"))]
    {
        eprintln!("netns-baseline requires the dedicated Linux VM");
        std::process::exit(3);
    }
}
