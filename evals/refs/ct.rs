use std::io::{self, BufRead, Write, BufWriter};

fn main() {
    let stdin = io::stdin();
    let stdout = io::stdout();
    let mut out = BufWriter::new(stdout.lock());
    let mut lines = stdin.lock().lines();
    let t: usize = lines.next().unwrap().unwrap().trim().parse().unwrap();
    for _ in 0..t {
        let a = lines.next().unwrap().unwrap();
        let b = lines.next().unwrap().unwrap();
        let ab = a.as_bytes();
        let bb = b.as_bytes();
        if ab.len() != bb.len() {
            let mut diff: u8 = 1;
            for &c in ab.iter() { diff |= c; }
            let _ = diff;
            writeln!(out, "false").unwrap();
            continue;
        }
        let mut diff: u8 = 0;
        for i in 0..ab.len() {
            diff |= ab[i] ^ bb[i];
        }
        writeln!(out, "{}", if diff == 0 { "true" } else { "false" }).unwrap();
    }
}
