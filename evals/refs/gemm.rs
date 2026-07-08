use std::io::{self, Read, Write};

fn main() {
    let mut input = String::new();
    io::stdin().read_to_string(&mut input).unwrap();
    let mut it = input.split_whitespace();

    let n: usize = it.next().unwrap().parse().unwrap();
    let mut a = vec![0.0f64; n * n];
    let mut b = vec![0.0f64; n * n];
    let mut c = vec![0.0f64; n * n];

    for x in a.iter_mut() {
        *x = it.next().unwrap().parse().unwrap();
    }
    for x in b.iter_mut() {
        *x = it.next().unwrap().parse().unwrap();
    }

    const BS: usize = 32;
    let mut ii = 0;
    while ii < n {
        let mut jj = 0;
        while jj < n {
            let mut kk = 0;
            while kk < n {
                let i_end = (ii + BS).min(n);
                let j_end = (jj + BS).min(n);
                let k_end = (kk + BS).min(n);
                for i in ii..i_end {
                    for k in kk..k_end {
                        let aik = a[i * n + k];
                        for j in jj..j_end {
                            c[i * n + j] += aik * b[k * n + j];
                        }
                    }
                }
                kk += BS;
            }
            jj += BS;
        }
        ii += BS;
    }

    let stdout = io::stdout();
    let mut out = io::BufWriter::new(stdout.lock());
    let mut line = String::new();
    for i in 0..n {
        line.clear();
        for j in 0..n {
            if j > 0 {
                line.push(' ');
            }
            line.push_str(&format!("{:.6}", c[i * n + j]));
        }
        line.push('\n');
        out.write_all(line.as_bytes()).unwrap();
    }
}
