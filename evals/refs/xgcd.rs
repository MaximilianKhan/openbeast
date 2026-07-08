use std::io::{self, Read, Write};

fn xgcd(a: i64, b: i64) -> (i64, i64, i64) {
    let (mut old_r, mut r) = (a, b);
    let (mut old_s, mut s) = (1i64, 0i64);
    let (mut old_t, mut t) = (0i64, 1i64);
    while r != 0 {
        let q = old_r / r;
        let tmp = old_r - q * r; old_r = r; r = tmp;
        let tmp = old_s - q * s; old_s = s; s = tmp;
        let tmp = old_t - q * t; old_t = t; t = tmp;
    }
    (old_r, old_s, old_t)
}

fn main() {
    let mut input = String::new();
    io::stdin().read_to_string(&mut input).unwrap();
    let mut nums = input.split_whitespace();
    let t: usize = nums.next().unwrap().parse().unwrap();
    let mut out = String::new();
    for _ in 0..t {
        let a: i64 = nums.next().unwrap().parse().unwrap();
        let b: i64 = nums.next().unwrap().parse().unwrap();
        let (g, x, y) = xgcd(a, b);
        out.push_str(&format!("{} {} {}\n", g, x, y));
    }
    io::stdout().write_all(out.as_bytes()).unwrap();
}
