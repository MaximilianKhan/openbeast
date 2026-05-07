use std::io::{self, BufRead, Write, BufWriter};

fn main() {
    let stdin = io::stdin();
    let stdout = io::stdout();
    let mut out = BufWriter::new(stdout.lock());
    let mut lines = stdin.lock().lines();
    let t: usize = lines.next().unwrap().unwrap().trim().parse().unwrap();
    for _ in 0..t {
        let n: usize = lines.next().unwrap().unwrap().trim().parse().unwrap();
        let vals_line = lines.next().unwrap().unwrap();
        if n == 0 {
            writeln!(out, "").unwrap();
            continue;
        }
        let toks: Vec<&str> = vals_line.split_ascii_whitespace().collect();
        let mut result: Vec<&str> = Vec::with_capacity(toks.len());
        let mut k: i64 = (toks.len() as i64) - 1;
        while k >= 0 {
            result.push(toks[k as usize]);
            k -= 1;
        }
        writeln!(out, "{}", result.join(" ")).unwrap();
    }
}
