package main

import (
	"bufio"
	"fmt"
	"math"
	"os"
	"strconv"
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
		N, _ := strconv.Atoi(read())
		M := make([][]float64, N)
		for i := 0; i < N; i++ {
			M[i] = make([]float64, N)
			for j := 0; j < N; j++ {
				v, _ := strconv.ParseFloat(read(), 64)
				M[i][j] = v
			}
		}
		sign := 1.0
		zero := false
		for col := 0; col < N; col++ {
			pivot := col
			for r := col + 1; r < N; r++ {
				if math.Abs(M[r][col]) > math.Abs(M[pivot][col]) {
					pivot = r
				}
			}
			if pivot != col {
				M[col], M[pivot] = M[pivot], M[col]
				sign = -sign
			}
			if math.Abs(M[col][col]) < 1e-15 {
				zero = true
				break
			}
			for r := col + 1; r < N; r++ {
				factor := M[r][col] / M[col][col]
				for c2 := col; c2 < N; c2++ {
					M[r][c2] -= factor * M[col][c2]
				}
			}
		}
		var det float64
		if zero {
			det = 0
		} else {
			det = sign
			for i := 0; i < N; i++ {
				det *= M[i][i]
			}
		}
		fmt.Fprintf(out, "%.6f\n", det)
	}
}
