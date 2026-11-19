# Algorithmic procedure

A procedure for any task where the algorithm decides whether the solution
fits its time limit. It names no task and no language.

1. Bound before code. From the stated input limits, write down the
   worst-case number of states the algorithm visits and the operations it
   performs. Rough rates: ~1e7 simple operations per second in Python, ~1e9
   in compiled code. If the estimate exceeds the time limit, choose another
   algorithm now, not after a timeout.
2. Decompose. Look for parts of the input that never interact. Solve each
   part on its own and combine the results: the cost becomes a sum of small
   searches instead of one product-sized search.
3. Test doubtful simplifications. When a split, a greedy choice or a
   dropped constraint would make the problem tractable but you are unsure
   it is sound, implement it and compare it with a brute force on many
   small random inputs before rejecting it. An argument is not a
   refutation; a counterexample is.
4. Search over orders or subsets as a depth-first search memoised on the
   minimal state that determines the future, not on the path that reached
   it. Restrict the candidates at each step with bounds that are guaranteed
   to hold.
5. Standard reductions: a sweep over sorted positions; prefix sums or
   difference arrays; binary search on the answer with a monotone check;
   bitmask DP up to ~20 items; meet-in-the-middle up to ~40.
6. Verify in two stages. First a tiny brute force that is obviously
   correct, compared on many random small inputs. Then a timing run at the
   full stated limits, including adversarial shapes (all equal, already
   sorted, maximal branching, the worst case for your pruning).
7. If a measurement misses the time limit by more than 5x, the algorithm is
   wrong: redesign, do not micro-optimise.
