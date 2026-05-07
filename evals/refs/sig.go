package main

import (
	"bufio"
	"fmt"
	"math"
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
		N, _ := strconv.Atoi(read())
		parts := make([]string, N)
		for i := 0; i < N; i++ {
			x, _ := strconv.ParseFloat(read(), 64)
			var s float64
			if x >= 0 {
				s = 1.0 / (1.0 + math.Exp(-x))
			} else {
				ex := math.Exp(x)
				s = ex / (1.0 + ex)
			}
			parts[i] = fmt.Sprintf("%.9f", s)
		}
		fmt.Fprintln(out, strings.Join(parts, " "))
	}
}
