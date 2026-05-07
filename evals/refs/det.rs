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
        let mut m = vec![vec![0.0f64; n]; n];
        for i in 0..n {
            for j in 0..n {
                m[i][j] = it.next().unwrap().parse().unwrap();
            }
        }
        let mut sign = 1.0f64;
        let mut zero = false;
        for col in 0..n {
            let mut pivot = col;
            for r in (col+1)..n {
                if m[r][col].abs() > m[pivot][col].abs() { pivot = r; }
            }
            if pivot != col {
                m.swap(col, pivot);
                sign = -sign;
            }
            if m[col][col].abs() < 1e-15 { zero = true; break; }
            for r in (col+1)..n {
                let factor = m[r][col] / m[col][col];
                for c2 in col..n { m[r][c2] -= factor * m[col][c2]; }
            }
        }
        let det = if zero {
            0.0
        } else {
            let mut d = sign;
            for i in 0..n { d *= m[i][i]; }
            d
        };
        writeln!(out, "{:.6}", det).unwrap();
    }
}
