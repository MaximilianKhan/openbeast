use std::io::{self, Read, Write, BufWriter};

fn main() {
    let mut s = String::new();
    io::stdin().read_to_string(&mut s).unwrap();
    let stdout = io::stdout();
    let mut out = BufWriter::new(stdout.lock());
    let mut it = s.split_ascii_whitespace();
    let t: usize = it.next().unwrap().parse().unwrap();
    for _ in 0..t {
        let h: usize = it.next().unwrap().parse().unwrap();
        let w: usize = it.next().unwrap().parse().unwrap();
        let mut a = vec![vec![0i64; w]; h];
        for i in 0..h {
            for j in 0..w {
                a[i][j] = it.next().unwrap().parse().unwrap();
            }
        }
        let mut at = vec![vec![0i64; h]; w];
        let bs = 16usize;
        let mut ii = 0usize;
        while ii < h {
            let mut jj = 0usize;
            while jj < w {
                let ie = (ii+bs).min(h);
                let je = (jj+bs).min(w);
                for i in ii..ie {
                    for j in jj..je {
                        at[j][i] = a[i][j];
                    }
                }
                jj += bs;
            }
            ii += bs;
        }
        for i in 0..w {
            for j in 0..h {
                if j > 0 { write!(out, " ").unwrap(); }
                write!(out, "{}", at[i][j]).unwrap();
            }
            writeln!(out, "").unwrap();
        }
    }
}
