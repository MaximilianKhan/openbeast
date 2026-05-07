use std::io::{self, Read, Write, BufWriter};

#[derive(Copy, Clone)]
struct C { re: f64, im: f64 }
impl std::ops::Add for C { type Output = C; fn add(self, r: C) -> C { C { re: self.re + r.re, im: self.im + r.im } } }
impl std::ops::Sub for C { type Output = C; fn sub(self, r: C) -> C { C { re: self.re - r.re, im: self.im - r.im } } }
impl std::ops::Mul for C { type Output = C; fn mul(self, r: C) -> C { C { re: self.re*r.re - self.im*r.im, im: self.re*r.im + self.im*r.re } } }

fn fft(x: &mut [C]) {
    let n = x.len();
    if n == 1 { return; }
    let mut e: Vec<C> = (0..n/2).map(|k| x[2*k]).collect();
    let mut o: Vec<C> = (0..n/2).map(|k| x[2*k+1]).collect();
    fft(&mut e); fft(&mut o);
    for k in 0..n/2 {
        let theta = -2.0 * std::f64::consts::PI * (k as f64) / (n as f64);
        let w = C { re: theta.cos(), im: theta.sin() };
        let t = w * o[k];
        x[k] = e[k] + t;
        x[k + n/2] = e[k] - t;
    }
}

fn main() {
    let mut s = String::new();
    io::stdin().read_to_string(&mut s).unwrap();
    let stdout = io::stdout();
    let mut out = BufWriter::new(stdout.lock());
    let mut it = s.split_ascii_whitespace();
    let t: usize = it.next().unwrap().parse().unwrap();
    for _ in 0..t {
        let n: usize = it.next().unwrap().parse().unwrap();
        let mut x: Vec<C> = (0..n).map(|_| {
            let v: f64 = it.next().unwrap().parse().unwrap();
            C { re: v, im: 0.0 }
        }).collect();
        fft(&mut x);
        for c in x {
            writeln!(out, "{:.4} {:.4}", c.re, c.im).unwrap();
        }
    }
}
