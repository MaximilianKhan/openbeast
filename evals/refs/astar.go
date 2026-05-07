package main

import (
	"bufio"
	"fmt"
	"os"
	"strconv"
	"strings"
)

func main() {
	in := bufio.NewReader(os.Stdin)
	out := bufio.NewWriter(os.Stdout)
	defer out.Flush()
	readLine := func() string {
		line, _ := in.ReadString('\n')
		return strings.TrimRight(line, "\n")
	}
	T, _ := strconv.Atoi(readLine())
	for c := 0; c < T; c++ {
		hw := strings.Fields(readLine())
		H, _ := strconv.Atoi(hw[0])
		W, _ := strconv.Atoi(hw[1])
		grid := make([]string, H)
		for i := 0; i < H; i++ {
			grid[i] = readLine()
		}
		sr, sc, gr, gc := -1, -1, -1, -1
		for r := 0; r < H; r++ {
			for col := 0; col < W; col++ {
				switch grid[r][col] {
				case 'S':
					sr, sc = r, col
				case 'G':
					gr, gc = r, col
				}
			}
		}
		if sr < 0 || gr < 0 {
			fmt.Fprintln(out, -1)
			continue
		}
		seen := make([][]bool, H)
		for i := range seen {
			seen[i] = make([]bool, W)
		}
		seen[sr][sc] = true
		type Pos struct{ r, c, d int }
		queue := []Pos{{sr, sc, 0}}
		head := 0
		found := -1
		dr := []int{-1, 1, 0, 0}
		dc := []int{0, 0, -1, 1}
		for head < len(queue) {
			p := queue[head]; head++
			if p.r == gr && p.c == gc {
				found = p.d; break
			}
			for k := 0; k < 4; k++ {
				nr, nc := p.r+dr[k], p.c+dc[k]
				if nr >= 0 && nr < H && nc >= 0 && nc < W && !seen[nr][nc] && grid[nr][nc] != '#' {
					seen[nr][nc] = true
					queue = append(queue, Pos{nr, nc, p.d + 1})
				}
			}
		}
		fmt.Fprintln(out, found)
	}
}
