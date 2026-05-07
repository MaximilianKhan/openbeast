use std::io::{self, Read, Write, BufWriter};

fn erf_as(x: f64) -> f64 {
    let p = 0.3275911;
    let a1 = 0.254829592;
    let a2 = -0.284496736;
    let a3 = 1.421413741;
    let a4 = -1.453152027;
    let a5 = 1.061405429;
    let sign = if x < 0.0 { -1.0 } else { 1.0 };
    let xa = x.abs();
    let t = 1.0 / (1.0 + p * xa);
    let y = 1.0 - (((((a5*t + a4)*t) + a3)*t + a2)*t + a1) * t * (-xa*xa).exp();
    sign * y
}

fn ncdf(x: f64) -> f64 {
    0.5 * (1.0 + erf_as(x / 2f64.sqrt()))
}

fn main() {
    let mut s = String::new();
    io::stdin().read_to_string(&mut s).unwrap();
    let stdout = io::stdout();
    let mut out = BufWriter::new(stdout.lock());
    let mut it = s.split_ascii_whitespace();
    let t: usize = it.next().unwrap().parse().unwrap();
    for _ in 0..t {
        let s_v: f64 = it.next().unwrap().parse().unwrap();
        let k: f64 = it.next().unwrap().parse().unwrap();
        let ty: f64 = it.next().unwrap().parse().unwrap();
        let r: f64 = it.next().unwrap().parse().unwrap();
        let sig: f64 = it.next().unwrap().parse().unwrap();
        let d1 = ((s_v/k).ln() + (r + 0.5*sig*sig)*ty) / (sig * ty.sqrt());
        let d2 = d1 - sig * ty.sqrt();
        let c = s_v * ncdf(d1) - k * (-r*ty).exp() * ncdf(d2);
        writeln!(out, "{:.4}", c).unwrap();
    }
}
