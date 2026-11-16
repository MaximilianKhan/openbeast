package main

import (
	"bufio"
	"fmt"
	"math"
	"os"
	"strconv"
)

func N(x float64) float64 {
	return 0.5 * (1 + math.Erf(x/math.Sqrt(2)))
}

func main() {
	in := bufio.NewScanner(os.Stdin)
	in.Buffer(make([]byte, 1<<20), 1<<20)
	in.Split(bufio.ScanWords)
	out := bufio.NewWriter(os.Stdout)
	defer out.Flush()
	read := func() string { in.Scan(); return in.Text() }
	T, _ := strconv.Atoi(read())
	for c := 0; c < T; c++ {
		S, _ := strconv.ParseFloat(read(), 64)
		K, _ := strconv.ParseFloat(read(), 64)
		Ty, _ := strconv.ParseFloat(read(), 64)
		r, _ := strconv.ParseFloat(read(), 64)
		sigma, _ := strconv.ParseFloat(read(), 64)
		d1 := (math.Log(S/K) + (r+0.5*sigma*sigma)*Ty) / (sigma * math.Sqrt(Ty))
		d2 := d1 - sigma*math.Sqrt(Ty)
		price := S*N(d1) - K*math.Exp(-r*Ty)*N(d2)
		fmt.Fprintf(out, "%.4f\n", price)
	}
}
