// SLOW (163_e): never finishes.
fn main() {
    let mut x: u64 = 0;
    loop {
        x = std::hint::black_box(x.wrapping_add(1));
    }
}
