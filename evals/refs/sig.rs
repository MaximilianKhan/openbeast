use std::io::{self, Read, Write, BufWriter};

fn main() {
    let mut s = String::new();
    io::stdin().read_to_string(&mut s).unwrap();
    let stdout = io::stdout();
    let mut out = BufWriter::new(stdout.lock());
    let mut it = s.split_ascii_whitespace();
    let t: usize = it.next().unwrap().parse().unwrap();
    for _ in 0..t {
        let n: usize = it.next().unwrap().parse().unwrap();
        let mut parts: Vec<String> = Vec::with_capacity(n);
        for _ in 0..n {
            let x: f64 = it.next().unwrap().parse().unwrap();
            let v = if x >= 0.0 {
                1.0 / (1.0 + (-x).exp())
            } else {
                let ex = x.exp();
                ex / (1.0 + ex)
            };
            parts.push(format!("{:.9}", v));
        }
        writeln!(out, "{}", parts.join(" ")).unwrap();
    }
}
