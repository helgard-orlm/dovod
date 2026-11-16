import time
def count_primes(n):
    sieve = [True] * (n + 1); count = 0
    for i in range(2, n + 1):
        if sieve[i]:
            count += 1
            j = i * i
            while j <= n:
                sieve[j] = False; j += i
    return count
def collatz_longest(limit):
    best = best_len = 1
    for start in range(1, limit + 1):
        x = start; steps = 1
        while x != 1:
            x = x // 2 if x % 2 == 0 else 3 * x + 1
            steps += 1
        if steps > best_len: best_len = steps; best = start
    return best
def digits_total(n):
    total = 0
    for i in range(n): total += len(str(i))
    return total
t0 = time.time(); print("primes below 5e6:", count_primes(5000000))
t1 = time.time(); print("longest collatz below 1e6:", collatz_longest(1000000))
t2 = time.time(); print("digits:", digits_total(2000000))
t3 = time.time(); print("ms: sieve %d, collatz %d, text %d" % ((t1-t0)*1000, (t2-t1)*1000, (t3-t2)*1000))
