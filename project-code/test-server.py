import subprocess
import time

END_MARK = "<<<END>>>"

def read_block(proc, timeout=10):
    lines, start = [], time.time()
    while True:
        if time.time() - start > timeout:
            break
        line = proc.stdout.readline()
        if not line:
            time.sleep(0.05)
            continue
        line = line.rstrip("\n")
        if line == END_MARK:
            break
        lines.append(line)
    return lines

def run_test():
    proc = subprocess.Popen(
        ["python", "-u", "server.py"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )

    tickers = ["AAPL", "TSLA", "MSFT"]

    print("=== Running Investment+Sentiment+News Tests ===\n")
    for t in tickers:
        print(f"You: {t}")
        proc.stdin.write(t + "\n")
        proc.stdin.flush()

        block = read_block(proc, timeout=12)
        if not block:
            print("⚠️  No output captured")
        else:
            for ln in block:
                print(ln)
        print("-" * 50)

    proc.terminate()
    print("\n=== Tests Complete ===")

if __name__ == "__main__":
    run_test()

