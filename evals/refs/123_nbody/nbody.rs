use std::io::{self, Read, Write, BufWriter};

fn accel(p: &[[f64;2]], mass: &[f64], n: usize) -> Vec<[f64;2]> {
    let mut a = vec![[0.0f64; 2]; n];
    for i in 0..n {
        for j in 0..n {
            if i == j { continue; }
            let dx = p[j][0] - p[i][0];
            let dy = p[j][1] - p[i][1];
            let r2 = dx*dx + dy*dy;
            let r = r2.sqrt();
            if r > 1e-12 {
                let f = mass[j] / (r2 * r);
                a[i][0] += f * dx;
                a[i][1] += f * dy;
            }
        }
    }
    a
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
        let dt: f64 = it.next().unwrap().parse().unwrap();
        let steps: usize = it.next().unwrap().parse().unwrap();
        let mut mass = vec![0.0f64; n];
        let mut pos = vec![[0.0f64;2]; n];
        let mut vel = vec![[0.0f64;2]; n];
        for i in 0..n {
            mass[i] = it.next().unwrap().parse().unwrap();
            pos[i][0] = it.next().unwrap().parse().unwrap();
            pos[i][1] = it.next().unwrap().parse().unwrap();
            vel[i][0] = it.next().unwrap().parse().unwrap();
            vel[i][1] = it.next().unwrap().parse().unwrap();
        }
        let mut a = accel(&pos, &mass, n);
        for _ in 0..steps {
            for i in 0..n {
                pos[i][0] += vel[i][0]*dt + 0.5*a[i][0]*dt*dt;
                pos[i][1] += vel[i][1]*dt + 0.5*a[i][1]*dt*dt;
            }
            let a2 = accel(&pos, &mass, n);
            for i in 0..n {
                vel[i][0] += 0.5*(a[i][0] + a2[i][0])*dt;
                vel[i][1] += 0.5*(a[i][1] + a2[i][1])*dt;
            }
            a = a2;
        }
        for i in 0..n {
            writeln!(out, "{:.6} {:.6} {:.6} {:.6}", pos[i][0], pos[i][1], vel[i][0], vel[i][1]).unwrap();
        }
    }
}
