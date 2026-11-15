// HANG PROBE: never terminates.
fn main() {
    let mut i: u64 = 0;
    loop {
        i = std::hint::black_box(i.wrapping_add(1));
    }
}
