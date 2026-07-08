use std::io::{self, Read, Write};

fn three_way_qs(a: &mut [i64], lo: isize, hi: isize) {
    if lo >= hi {
        return;
    }
    let pivot = a[(lo + (hi - lo) / 2) as usize];
    let (mut lt, mut i, mut gt) = (lo, lo, hi);
    while i <= gt {
        let v = a[i as usize];
        if v < pivot {
            a.swap(lt as usize, i as usize);
            lt += 1;
            i += 1;
        } else if v > pivot {
            a.swap(gt as usize, i as usize);
            gt -= 1;
        } else {
            i += 1;
        }
    }
    three_way_qs(a, lo, lt - 1);
    three_way_qs(a, gt + 1, hi);
}

fn main() {
    let mut input = String::new();
    io::stdin().read_to_string(&mut input).unwrap();
    let mut it = input.split_ascii_whitespace();
    let t: usize = it.next().unwrap().parse().unwrap();
    let mut out = String::new();
    for _ in 0..t {
        let n: usize = it.next().unwrap().parse().unwrap();
        let mut arr: Vec<i64> = (0..n)
            .map(|_| it.next().unwrap().parse().unwrap())
            .collect();
        if n > 0 {
            three_way_qs(&mut arr, 0, (n - 1) as isize);
        }
        let line: Vec<String> = arr.iter().map(|x| x.to_string()).collect();
        out.push_str(&line.join(" "));
        out.push('\n');
    }
    io::stdout().write_all(out.as_bytes()).unwrap();
}
