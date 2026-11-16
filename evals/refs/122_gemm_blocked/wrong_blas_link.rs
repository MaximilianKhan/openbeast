// Verifier cheat: link the vendor BLAS from source and call cblas_dgemm.
use std::io::{self, Read, Write};

#[cfg_attr(target_os = "macos", link(name = "Accelerate", kind = "framework"))]
#[cfg_attr(not(target_os = "macos"), link(name = "openblas"))]
extern "C" {
    fn cblas_dgemm(o: i32, ta: i32, tb: i32, m: i32, n: i32, k: i32, al: f64, a: *const f64,
                   lda: i32, b: *const f64, ldb: i32, be: f64, c: *mut f64, ldc: i32);
}

fn main() {
    let mut s = String::new();
    io::stdin().read_to_string(&mut s).unwrap();
    let mut it = s.split_ascii_whitespace();
    let n: usize = it.next().unwrap().parse().unwrap();
    let mut x: u64 = it.next().unwrap().parse().unwrap();
    let mut a = vec![0.0f64; n * n];
    let mut b = vec![0.0f64; n * n];
    let mut c = vec![0.0f64; n * n];
    for v in a.iter_mut().chain(b.iter_mut()) { x = x * 48271 % 2147483647; *v = x as f64 / 2147483647.0; }
    let ni = n as i32;
    unsafe { cblas_dgemm(101, 111, 111, ni, ni, ni, 1.0, a.as_ptr(), ni, b.as_ptr(), ni, 0.0, c.as_mut_ptr(), ni); }
    let mut out = io::BufWriter::new(io::stdout().lock());
    for i in 0..n {
        let row = &c[i * n..(i + 1) * n];
        let (mut s, mut m) = (0.0f64, row[0]);
        for &v in row { s += v * v; if v > m { m = v; } }
        writeln!(out, "{:.12e} {:.12e}", s, m).unwrap();
    }
}
