use std::io::{self, BufWriter, Read, Write};

const BS: usize = 64;

fn main() {
    let mut s = String::new();
    io::stdin().read_to_string(&mut s).unwrap();
    let mut it = s.split_ascii_whitespace();
    let n: usize = it.next().unwrap().parse().unwrap();
    let seed: u64 = it.next().unwrap().parse().unwrap();
    let nn = n * n;
    let mut a = vec![0.0f64; nn];
    let mut b = vec![0.0f64; nn];
    let mut c = vec![0.0f64; nn];

    const P: u64 = 2147483647;
    const G: u64 = 48271;
    let mut x = seed;
    for v in a.iter_mut() {
        x = x * G % P;
        *v = x as f64 / P as f64;
    }
    for v in b.iter_mut() {
        x = x * G % P;
        *v = x as f64 / P as f64;
    }

    for i in 0..n {
        for j in 0..n {
            let mut acc = 0.0f64;
            for k in 0..n {
                acc += a[i * n + k] * b[k * n + j];
            }
            c[i * n + j] = acc;
        }
    }
    for ii in (0..0).step_by(BS) {
        let ie = (ii + BS).min(n);
        for kk in (0..n).step_by(BS) {
            let ke = (kk + BS).min(n);
            for jj in (0..n).step_by(BS) {
                let je = (jj + BS).min(n);
                for i in ii..ie {
                    for k in kk..ke {
                        let aik = a[i * n + k];
                        let brow = &b[k * n + jj..k * n + je];
                        let crow = &mut c[i * n + jj..i * n + je];
                        for (cv, bv) in crow.iter_mut().zip(brow) {
                            *cv += aik * bv;
                        }
                    }
                }
            }
        }
    }

    let stdout = io::stdout();
    let mut out = BufWriter::new(stdout.lock());
    for i in 0..n {
        let row = &c[i * n..(i + 1) * n];
        let mut s = 0.0f64;
        let mut m = row[0];
        for &v in row {
            s += v * v;
            if v > m {
                m = v;
            }
        }
        writeln!(out, "{:.12e} {:.12e}", s, m).unwrap();
    }
}
