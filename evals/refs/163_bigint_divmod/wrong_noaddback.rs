// WRONG (163_e): never adds back (D6).
use std::io::{self, Read, Write};

fn trim(a: &mut Vec<u32>) {
    while a.len() > 1 && *a.last().unwrap() == 0 {
        a.pop();
    }
}

fn divmod_d(mut u: Vec<u32>, mut v: Vec<u32>) -> (Vec<u32>, Vec<u32>, u64, u64) {
    while u.last() == Some(&0) {
        u.pop();
    }
    trim(&mut v);
    let n = v.len();
    let lu = u.len();
    if lu < n {
        let r = if u.is_empty() { vec![0] } else { u };
        return (vec![0], r, 0, 0);
    }
    if n == 1 {
        let d = v[0] as u64;
        let mut q = vec![0u32; lu];
        let mut rem = 0u64;
        for i in (0..lu).rev() {
            let cur = (rem << 32) | u[i] as u64;
            q[i] = (cur / d) as u32;
            rem = cur % d;
        }
        trim(&mut q);
        return (q, vec![rem as u32], 0, 0);
    }
    let m = lu - n;
    let s = v[n - 1].leading_zeros();
    let shl = |hi: u32, lo: u32| -> u32 { ((((hi as u64) << 32 | lo as u64) << s) >> 32) as u32 };
    let mut vn = vec![0u32; n];
    for i in (1..n).rev() {
        vn[i] = shl(v[i], v[i - 1]);
    }
    vn[0] = shl(v[0], 0);
    let mut un = vec![0u32; lu + 1];
    un[lu] = shl(0, u[lu - 1]);
    for i in (1..lu).rev() {
        un[i] = shl(u[i], u[i - 1]);
    }
    un[0] = shl(u[0], 0);
    let b: u64 = 1 << 32;
    let v1 = vn[n - 1] as u64;
    let v2 = vn[n - 2] as u64;
    let mut q = vec![0u32; m + 1];
    let (mut d3, mut d6) = (0u64, 0u64);
    for j in (0..=m).rev() {
        let num = (un[j + n] as u64) << 32 | un[j + n - 1] as u64;
        let mut qhat = num / v1;
        let mut rhat = num % v1;
        while qhat >= b || qhat * v2 > ((rhat << 32) | un[j + n - 2] as u64) {
            qhat -= 1;
            d3 += 1;
            rhat += v1;
            if rhat >= b {
                break;
            }
        }
        let mut carry = 0u64;
        let mut borrow = 0u64;
        for i in 0..n {
            let p = qhat * vn[i] as u64 + carry;
            carry = p >> 32;
            let t = (un[i + j] as u64).wrapping_sub(p & 0xFFFF_FFFF).wrapping_sub(borrow);
            un[i + j] = t as u32;
            borrow = (t >> 32) & 1;
        }
        let t = (un[j + n] as u64).wrapping_sub(carry).wrapping_sub(borrow);
        un[j + n] = t as u32;
        if false && t >> 63 != 0 {
            d6 += 1;
            qhat -= 1;
            let mut c = 0u64;
            for i in 0..n {
                let sum = un[i + j] as u64 + vn[i] as u64 + c;
                un[i + j] = sum as u32;
                c = sum >> 32;
            }
            un[j + n] = un[j + n].wrapping_add(c as u32);
        }
        q[j] = qhat as u32;
    }
    let mut r = vec![0u32; n];
    for i in 0..n {
        let hi = if i + 1 < n { un[i + 1] as u64 } else { 0 };
        r[i] = (((hi << 32) | un[i] as u64) >> s) as u32;
    }
    trim(&mut q);
    trim(&mut r);
    (q, r, d3, d6)
}

fn main() {
    let mut data = String::new();
    io::stdin().read_to_string(&mut data).unwrap();
    let mut it = data.split_ascii_whitespace().map(|t| t.parse::<u64>().unwrap());
    let t = it.next().unwrap();
    let mut out = String::new();
    let put = |out: &mut String, a: &[u32]| {
        for (i, x) in a.iter().enumerate() {
            if i > 0 {
                out.push(' ');
            }
            out.push_str(&x.to_string());
        }
        out.push('\n');
    };
    for _ in 0..t {
        let lu = it.next().unwrap() as usize;
        let lv = it.next().unwrap() as usize;
        let u: Vec<u32> = (0..lu).map(|_| it.next().unwrap() as u32).collect();
        let v: Vec<u32> = (0..lv).map(|_| it.next().unwrap() as u32).collect();
        let (q, r, d3, d6) = divmod_d(u, v);
        put(&mut out, &q);
        put(&mut out, &r);
        out.push_str(&format!("{} {}\n", d3, d6));
    }
    io::stdout().write_all(out.as_bytes()).unwrap();
}
