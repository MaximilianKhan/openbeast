use std::io::{self, Read, Write, BufWriter};

fn main() {
    let mut s = String::new();
    io::stdin().read_to_string(&mut s).unwrap();
    let stdout = io::stdout();
    let mut out = BufWriter::new(stdout.lock());
    let mut it = s.split_ascii_whitespace();
    let t: usize = it.next().unwrap().parse().unwrap();
    for _ in 0..t {
        let mut n: u64 = it.next().unwrap().parse().unwrap();
        let mut count: u32 = 0;
        while n > 0 {
            n &= n - 1;
            count += 1;
        }
        writeln!(out, "{}", count).unwrap();
    }
}
