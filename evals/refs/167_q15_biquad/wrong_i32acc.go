// WRONG: 32-bit wrapping accumulator in the A=0 path.
package main

import (
	"bufio"
	"os"
	"strconv"
)

func c32(v int64) int64 {
	if v > 2147483647 {
		return 2147483647
	}
	if v < -2147483648 {
		return -2147483648
	}
	return v
}

func main() {
	sc := bufio.NewScanner(os.Stdin)
	sc.Buffer(make([]byte, 1<<20), 1<<20)
	sc.Split(bufio.ScanWords)
	next := func() int64 {
		sc.Scan()
		v, _ := strconv.ParseInt(sc.Text(), 10, 64)
		return v
	}
	w := bufio.NewWriter(os.Stdout)
	defer w.Flush()
	t := int(next())
	for tc := 0; tc < t; tc++ {
		am, om, ns, n := next(), next(), int(next()), int(next())
		c := make([][6]int64, ns)
		for s := 0; s < ns; s++ {
			for j := 0; j < 6; j++ {
				c[s][j] = next()
			}
		}
		buf := make([]int64, n)
		for i := range buf {
			buf[i] = next()
		}
		for _, sec := range c {
			b0, b1, b2, a1, a2 := sec[0], sec[1], sec[2], sec[3], sec[4]
			sh := uint(15 - sec[5])
			rnd := int64(1) << (sh - 1)
			var x1, x2, y1, y2 int64
			for i, x := range buf {
				var acc int64
				if am != 0 {
					acc = c32(b0 * x)
					acc = c32(acc + b1*x1)
					acc = c32(acc + b2*x2)
					acc = c32(acc - a1*y1)
					acc = c32(acc - a2*y2)
				} else {
					acc = int64(int32(b0)*int32(x) + int32(b1)*int32(x1) + int32(b2)*int32(x2) - int32(a1)*int32(y1) - int32(a2)*int32(y2))
				}
				v := (acc + rnd) >> sh
				var y int64
				if om != 0 {
					y = ((v + 32768) & 0xFFFF) - 32768
				} else if v > 32767 {
					y = 32767
				} else if v < -32768 {
					y = -32768
				} else {
					y = v
				}
				x2, x1, y2, y1 = x1, x, y1, y
				buf[i] = y
			}
		}
		for i, v := range buf {
			if i > 0 {
				w.WriteByte(' ')
			}
			w.WriteString(strconv.FormatInt(v, 10))
		}
		w.WriteByte('\n')
	}
}
