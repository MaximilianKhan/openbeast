use std::io::{self, BufRead, Write, BufWriter};
use std::collections::VecDeque;

fn main() {
    let stdin = io::stdin();
    let stdout = io::stdout();
    let mut out = BufWriter::new(stdout.lock());
    let mut lines = stdin.lock().lines();
    let t: usize = lines.next().unwrap().unwrap().trim().parse().unwrap();
    for _ in 0..t {
        let hw_line = lines.next().unwrap().unwrap();
        let mut it = hw_line.split_ascii_whitespace();
        let h: usize = it.next().unwrap().parse().unwrap();
        let w: usize = it.next().unwrap().parse().unwrap();
        let mut grid: Vec<Vec<u8>> = Vec::with_capacity(h);
        for _ in 0..h {
            let row = lines.next().unwrap().unwrap();
            grid.push(row.into_bytes());
        }
        let mut sr: i32 = -1; let mut sc: i32 = -1;
        let mut gr: i32 = -1; let mut gc: i32 = -1;
        for r in 0..h {
            for c in 0..w {
                if grid[r][c] == b'S' { sr = r as i32; sc = c as i32; }
                else if grid[r][c] == b'G' { gr = r as i32; gc = c as i32; }
            }
        }
        if sr < 0 || gr < 0 { writeln!(out, "{}", -1).unwrap(); continue; }
        let mut seen = vec![vec![false; w]; h];
        seen[sr as usize][sc as usize] = true;
        let mut q: VecDeque<(i32,i32,i32)> = VecDeque::new();
        q.push_back((sr, sc, 0));
        let mut found: i32 = -1;
        let dr = [-1i32, 1, 0, 0];
        let dc = [0i32, 0, -1, 1];
        while let Some((r, c, d)) = q.pop_front() {
            if r == gr && c == gc { found = d; break; }
            for k in 0..4 {
                let nr = r + dr[k]; let nc = c + dc[k];
                if nr >= 0 && nr < h as i32 && nc >= 0 && nc < w as i32
                   && !seen[nr as usize][nc as usize]
                   && grid[nr as usize][nc as usize] != b'#' {
                    seen[nr as usize][nc as usize] = true;
                    q.push_back((nr, nc, d+1));
                }
            }
        }
        writeln!(out, "{}", found).unwrap();
    }
}
