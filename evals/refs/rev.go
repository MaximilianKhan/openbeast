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
		N, _ := strconv.Atoi(readLine())
		valsLine := readLine()
		if N == 0 {
			fmt.Fprintln(out, "")
			continue
		}
		nums := strings.Fields(valsLine)
		result := make([]string, len(nums))
		k := len(nums) - 1
		w := 0
		for k >= 0 {
			result[w] = nums[k]
			w++
			k--
		}
		fmt.Fprintln(out, strings.Join(result, " "))
	}
}
