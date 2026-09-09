"""Finite exact acceptance example; not a theorem proof."""
checked = 0
for n in range(1, 201):
    assert n * (n + 1) % 2 == 0, n
    checked += 1
assert checked == 200
print('AILAB_TEST={"status":"PASS","checked_points":200}')
