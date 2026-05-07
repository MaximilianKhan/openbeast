package main

import (
	"bufio"
	"fmt"
	"os"
	"strconv"
	"strings"
)

func main() {
	in := bufio.NewScanner(os.Stdin)
	in.Buffer(make([]byte, 1<<20), 1<<20)
	in.Split(bufio.ScanWords)
	out := bufio.NewWriter(os.Stdout)
	defer out.Flush()
	read := func() string { in.Scan(); return in.Text() }

	T, _ := strconv.Atoi(read())
	for c := 0; c < T; c++ {
		H, _ := strconv.Atoi(read())
		W, _ := strconv.Atoi(read())
		A := make([][]int, H)
		for i := 0; i < H; i++ {
			A[i] = make([]int, W)
			for j := 0; j < W; j++ {
				v, _ := strconv.Atoi(read())
				A[i][j] = v
			}
		}
		AT := make([][]int, W)
		for i := 0; i < W; i++ {
			AT[i] = make([]int, H)
		}
		BS := 16
		for ii := 0; ii < H; ii += BS {
			for jj := 0; jj < W; jj += BS {
				ie := ii + BS
				if ie > H {
					ie = H
				}
				je := jj + BS
				if je > W {
					je = W
				}
				for i := ii; i < ie; i++ {
					for j := jj; j < je; j++ {
						AT[j][i] = A[i][j]
					}
				}
			}
		}
		for _, row := range AT {
			parts := make([]string, len(row))
			for k, v := range row {
				parts[k] = strconv.Itoa(v)
			}
			fmt.Fprintln(out, strings.Join(parts, " "))
		}
	}
}
