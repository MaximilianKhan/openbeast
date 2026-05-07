use std::io::{self, Read, Write, BufWriter};

fn main() {
    let mut s = String::new();
    io::stdin().read_to_string(&mut s).unwrap();
    let stdout = io::stdout();
    let mut out = BufWriter::new(stdout.lock());
    let mut it = s.split_ascii_whitespace();
    let t: usize = it.next().unwrap().parse().unwrap();
    const MULT: u64 = 6364136223846793005;
    const INC: u64 = 1442695040888963407;
    const R2: u64 = (1u64 << 30) * (1u64 << 30);
    for _ in 0..t {
        let n: u64 = it.next().unwrap().parse().unwrap();
        let seed: u64 = it.next().unwrap().parse().unwrap();
        let mut state = seed;
        let mut count: u64 = 0;
        for _ in 0..n {
            state = state.wrapping_mul(MULT).wrapping_add(INC);
            let x = (state >> 33) & 0x3FFFFFFF;
            state = state.wrapping_mul(MULT).wrapping_add(INC);
            let y = (state >> 33) & 0x3FFFFFFF;
            if x*x + y*y < R2 { count += 1; }
        }
        writeln!(out, "{}", count).unwrap();
    }
}
