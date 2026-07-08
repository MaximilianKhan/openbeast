package main

import (
	"bufio"
	"os"
	"strconv"
)

func threeWayQS(a []int64, lo, hi int) {
	if lo >= hi {
		return
	}
	pivot := a[lo+(hi-lo)/2]
	lt, i, gt := lo, lo, hi
	for i <= gt {
		if a[i] < pivot {
			a[lt], a[i] = a[i], a[lt]
			lt++
			i++
		} else if a[i] > pivot {
			a[gt], a[i] = a[i], a[gt]
			gt--
		} else {
			i++
		}
	}
	threeWayQS(a, lo, lt-1)
	threeWayQS(a, gt+1, hi)
}

func main() {
	reader := bufio.NewReader(os.Stdin)
	writer := bufio.NewWriter(os.Stdout)
	defer writer.Flush()

	readInt := func() int64 {
		n := int64(0)
		neg := false
		c, err := reader.ReadByte()
		for err == nil && (c == ' ' || c == '\n' || c == '\r' || c == '\t') {
			c, err = reader.ReadByte()
		}
		if c == '-' {
			neg = true
			c, err = reader.ReadByte()
		}
		for err == nil && c >= '0' && c <= '9' {
			n = n*10 + int64(c-'0')
			c, err = reader.ReadByte()
		}
		if neg {
			return -n
		}
		return n
	}

	t := readInt()
	for ; t > 0; t-- {
		n := readInt()
		arr := make([]int64, n)
		for i := int64(0); i < n; i++ {
			arr[i] = readInt()
		}
		if n > 0 {
			threeWayQS(arr, 0, int(n)-1)
		}
		for i := int64(0); i < n; i++ {
			if i > 0 {
				writer.WriteByte(' ')
			}
			writer.WriteString(strconv.FormatInt(arr[i], 10))
		}
		writer.WriteByte('\n')
	}
}
