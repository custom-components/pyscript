"""Unit tests for Python interpreter."""

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.pyscript.const import CONF_ALLOW_ALL_IMPORTS, CONFIG_ENTRY, DOMAIN
from custom_components.pyscript.eval import AstEval
from custom_components.pyscript.function import Function
from custom_components.pyscript.global_ctx import GlobalContext, GlobalContextMgr
from custom_components.pyscript.state import State
from custom_components.pyscript.trigger import TrigTime

evalTests = [
    ["1", 1],
    ["1+1", 2],
    ["1+2*3-2", 5],
    ["1-1", 0],
    ["4/2", 2],
    ["4**2", 16],
    ["4<<2", 16],
    ["16>>2", 4],
    ["18 ^ 2", 16],
    ["16 | 2", 18],
    ["0x37 & 0x6c ", 0x24],
    ["11 // 2", 5],
    ["not True", False],
    ["not False", True],
    ["z = 1+2+3; a = z + 1; a + 3", 10],
    ["z = 1+2+3; a = z + 1; a - 3", 4],
    ["x = 1; -x", -1],
    ["z = 5; +z", 5],
    ["~0xff", -256],
    ["x = 1; x < 2", 1],
    ["x = 1; x <= 1", 1],
    ["x = 1; 0 < x < 2", 1],
    ["x = 1; 2 > x > 0", 1],
    ["x = 1; 2 > x >= 1", 1],
    ["x = 1; 0 < x < 2 < -x", 0],
    ["x = [1,2,3]; del x[1:2]; x", [1, 3]],
    ["x = [1,2,3]; del x[1::]; x", [1]],
    ["1 and 2", 2],
    ["1 and 0", 0],
    ["0 or 1", 1],
    ["0 or 0", 0],
    ["1 and True", True],
    ["1 and False", False],
    ["0 and False", 0],
    ["None or 2", 2],
    ["False or 3", 3],
    ["None or 'xyz'", "xyz"],
    ["False or 'xyz'", "xyz"],
    ["0 or 1", 1],
    ["0 or 0", 0],
    ["0 or True", True],
    ["0 or False", False],
    ["False or True", True],
    ["False or False", False],
    ["isinstance(False or False, bool)", True],
    ["isinstance(False or 0, int)", True],
    ["f'{1} {2:02d} {3:.1f}'", "1 02 3.0"],
    [["x = None", "x is None"], True],
    ["None is not None", False],
    ["10 in {5, 9, 10, 20}", True],
    ["10 not in {5, 9, 10, 20}", False],
    ["sym_local + 10", 20],
    ["z = 'foo'; z + 'bar'", "foobar"],
    ["xyz.y = 5; xyz.y = 2 + int(xyz.y); int(xyz.y)", 7],
    ["xyz.y = 'bar'; xyz.y += '2'; xyz.y", "bar2"],
    ["z = 'abcd'; z.find('c')", 2],
    ["'abcd'.upper().lower().upper()", "ABCD"],
    ["len('abcd')", 4],
    ["6 if 1-1 else 2", 2],
    ["x = 1; x += 3; x", 4],
    ["z = [1,2,3]; [z[1], z[-1]]", [2, 3]],
    ["'{1} {0}'.format('one', 'two')", "two one"],
    ["'%d, %d' % (23, 45)", "23, 45"],
    ["x = [[1,2,3]]; sum(*x)", 6],
    ["args = [1, 5, 10]; {6, *args, 15}", {1, 5, 6, 10, 15}],
    ["args = [1, 5, 10]; [6, *args, 15]", [6, 1, 5, 10, 15]],
    ["kw = {'x': 1, 'y': 5}; {**kw}", {"x": 1, "y": 5}],
    ["kw = {'x': 1, 'y': 5}; kw2 = {'z': 10}; {**kw, **kw2}", {"x": 1, "y": 5, "z": 10}],
    ["[*iter([1, 2, 3])]", [1, 2, 3]],
    ["{*iter([1, 2, 3])}", {1, 2, 3}],
    ["[x for x in []]", []],
    ["[x for x in [] if x]", []],
    ["if 1: x = 10\nelse: x = 20\nx", 10],
    ["if 0: x = 10\nelse: x = 20\nx", 20],
    ["i = 0\nwhile i < 5: i += 1\ni", 5],
    ["i = 0\nwhile i < 5: i += 2\ni", 6],
    ["i = 0\nwhile i < 5:\n    i += 1\n    if i == 3: break\n2 * i", 6],
    ["i = 0; k = 10\nwhile i < 5:\n    i += 1\n    if i <= 2: continue\n    k += 1\nk + i", 18],
    ["i = 1; break; i = 1/0", None],
    ["s = 0;\nfor i in range(5):\n    s += i\ns", 10],
    ["s = 0;\nfor i in iter([10,20,30]):\n    s += i\ns", 60],
    ["z = {'foo': 'bar', 'foo2': 12}; z['foo'] = 'bar2'; z", {"foo": "bar2", "foo2": 12}],
    ["z = {'foo': 'bar', 'foo2': 12}; z['foo'] = 'bar2'; z.keys()", {"foo", "foo2"}],
    ["z = {'foo', 'bar', 12}; z", {"foo", "bar", 12}],
    ["x = dict(key1 = 'value1', key2 = 'value2'); x", {"key1": "value1", "key2": "value2"}],
    [
        "x = dict(key1 = 'value1', key2 = 'value2', key3 = 'value3'); del x['key1']; x",
        {"key2": "value2", "key3": "value3"},
    ],
    ["z = {'foo', 'bar', 12}; z.remove(12); z.add(20); z", {"foo", "bar", 20}],
    ["z = [0, 1, 2, 3, 4, 5, 6]; z[1:5:2] = [4, 5]; z", [0, 4, 2, 5, 4, 5, 6]],
    ["[0, 1, 2, 3, 4, 5, 6, 7, 8][1:5:2]", [1, 3]],
    ["[0, 1, 2, 3, 4, 5, 6, 7, 8][1:5]", [1, 2, 3, 4]],
    ["[0, 1, 2, 3, 4, 5, 6, 7, 8][1::3]", [1, 4, 7]],
    ["[0, 1, 2, 3, 4, 5, 6, 7, 8][4::]", [4, 5, 6, 7, 8]],
    ["[0, 1, 2, 3, 4, 5, 6, 7, 8][4:]", [4, 5, 6, 7, 8]],
    ["[0, 1, 2, 3, 4, 5, 6, 7, 8][:6:2]", [0, 2, 4]],
    ["[0, 1, 2, 3, 4, 5, 6, 7, 8][:4:]", [0, 1, 2, 3]],
    ["[0, 1, 2, 3, 4, 5, 6, 7, 8][::2]", [0, 2, 4, 6, 8]],
    ["[0, 1, 2, 3, 4, 5, 6, 7, 8][::]", [0, 1, 2, 3, 4, 5, 6, 7, 8]],
    ["z = [0, 1, 2, 3, 4, 5, 6, 7, 8]; z[1:5:2] = [6, 8]; z", [0, 6, 2, 8, 4, 5, 6, 7, 8]],
    ["z = [0, 1, 2, 3, 4, 5, 6, 7, 8]; z[1:5] = [10, 11]; z", [0, 10, 11, 5, 6, 7, 8]],
    ["z = [0, 1, 2, 3, 4, 5, 6, 7, 8]; z[1::3] = [10, 11, 12]; z", [0, 10, 2, 3, 11, 5, 6, 12, 8]],
    ["z = [0, 1, 2, 3, 4, 5, 6, 7, 8]; z[4::] = [10, 11, 12, 13]; z", [0, 1, 2, 3, 10, 11, 12, 13]],
    ["z = [0, 1, 2, 3, 4, 5, 6, 7, 8]; z[4:] = [10, 11, 12, 13, 14]; z", [0, 1, 2, 3, 10, 11, 12, 13, 14]],
    ["z = [0, 1, 2, 3, 4, 5, 6, 7, 8]; z[:6:2] = [10, 11, 12]; z", [10, 1, 11, 3, 12, 5, 6, 7, 8]],
    ["z = [0, 1, 2, 3, 4, 5, 6, 7, 8]; z[:4:] = [10, 11, 12, 13]; z", [10, 11, 12, 13, 4, 5, 6, 7, 8]],
    ["z = [0, 1, 2, 3, 4, 5, 6, 7, 8]; z[::2] = [10, 11, 12, 13, 14]; z", [10, 1, 11, 3, 12, 5, 13, 7, 14]],
    [
        "z = [0, 1, 2, 3, 4, 5, 6, 7, 8]; z[::] = [10, 11, 12, 13, 14, 15, 16, 17]; z",
        [10, 11, 12, 13, 14, 15, 16, 17],
    ],
    ["(x, y) = (1, 2); [x, y]", [1, 2]],
    ["a, b = (x, y) = (1, 2); [a, b, x, y]", [1, 2, 1, 2]],
    ["y = [1,2]; (x, y[0]) = (3, 4); [x, y]", [3, [4, 2]]],
    ["((x, y), (z, t)) = ((1, 2), (3, 4)); [x, y, z, t]", [1, 2, 3, 4]],
    ["z = [1,2,3]; ((x, y), (z[2], t)) = ((1, 2), (20, 4)); [x, y, z, t]", [1, 2, [1, 2, 20], 4]],
    ["a, b, c = [1,2,3]; [a, b, c]", [1, 2, 3]],
    ["a, b, c = iter([1,2,3]); [a, b, c]", [1, 2, 3]],
    ["tuples = [(1, 2), (3, 4), (5, 6)]; a, b = zip(*tuples); [a, b]", [(1, 3, 5), (2, 4, 6)]],
    ["a, *y, w, z = range(3); [a, y, w, z]", [0, [], 1, 2]],
    ["a, *y, w, z = range(4); [a, y, w, z]", [0, [1], 2, 3]],
    ["a, *y, w, z = range(6); [a, y, w, z]", [0, [1, 2, 3], 4, 5]],
    ["x = [0, 1]; i = 0; i, x[i] = 1, 2; [i, x]", [1, [0, 2]]],
    ["d = {'x': 123}; d['x'] += 10; d", {"x": 133}],
    ["d = [20, 30]; d[1] += 10; d", [20, 40]],
    ["func = lambda m=2: 2 * m; [func(), func(3), func(m=4)]", [4, 6, 8]],
    ["thres = 1; list(filter(lambda x: x < thres, range(-5, 5)))", [-5, -4, -3, -2, -1, 0]],
    ["y = 5; y = y + (x := 2 * y); [x, y]", [10, 15]],
    ["x: int = 10; x", 10],
    ["x: int = [10, 20]; x", [10, 20]],
    ["Foo = type('Foo', (), {'x': 100}); Foo.x = 10; Foo.x", 10],
    ["Foo = type('Foo', (), {'x': 100}); Foo.x += 10; Foo.x", 110],
    ["Foo = [type('Foo', (), {'x': 100})]; Foo[0].x = 10; Foo[0].x", 10],
    ["Foo = [type('Foo', (), {'x': [100, 101]})]; Foo[0].x[1] = 10; Foo[0].x", [100, 10]],
    ["Foo = [type('Foo', (), {'x': [0, [[100, 101]]]})]; Foo[0].x[1][0][1] = 10; Foo[0].x[1]", [[100, 10]]],
    [
        "Foo = [type('Foo', (), {'x': [0, [[100, 101, 102, 103]]]})]; Foo[0].x[1][0][1:2] = [11, 12]; Foo[0].x[1]",
        [[100, 11, 12, 102, 103]],
    ],
    [
        "pyscript.var1 = 1; pyscript.var2 = 2; set(state.names('pyscript'))",
        {"pyscript.var1", "pyscript.var2"},
    ],
    [
        """
state.set("pyscript.var1", 100, attr1=1, attr2=3.5)
chk = [[pyscript.var1.attr1, pyscript.var1.attr2]]
pyscript.var1 += "xyz"
chk.append([pyscript.var1, pyscript.var1.attr1, pyscript.var1.attr2])
state.set("pyscript.var1", 200, attr3 = 'abc')
chk.append([pyscript.var1.attr1, pyscript.var1.attr2, pyscript.var1.attr3])
chk.append(state.getattr("pyscript.var1"))
del pyscript.var1.attr3
chk.append(state.getattr("pyscript.var1"))
state.delete("pyscript.var1.attr2")
chk.append(state.getattr("pyscript.var1"))
state.set("pyscript.var1", pyscript.var1, {})
chk.append(state.getattr("pyscript.var1"))
state.set("pyscript.var1", pyscript.var1, new_attributes={"attr2": 8.5, "attr3": "xyz"})
chk.append(state.getattr("pyscript.var1"))
pyscript.var1.attr2 = "abc"
chk.append(state.getattr(pyscript.var1))
state.set("pyscript.var1", attr1=123)
state.set("pyscript.var1", attr3="def")
chk.append(state.getattr("pyscript.var1"))
pyscript.var1.attr4 = 987
chk.append(state.getattr(pyscript.var1))
state.set("pyscript.var1", new_attributes={"attr2": 9.5, "attr3": "xyz"})
chk.append(state.getattr("pyscript.var1"))
chk.append(pyscript.var1)
chk
""",
        [
            [1, 3.5],
            ["100xyz", 1, 3.5],
            [1, 3.5, "abc"],
            {"attr1": 1, "attr2": 3.5, "attr3": "abc"},
            {"attr1": 1, "attr2": 3.5},
            {"attr1": 1},
            {},
            {"attr2": 8.5, "attr3": "xyz"},
            {"attr2": "abc", "attr3": "xyz"},
            {"attr1": 123, "attr2": "abc", "attr3": "def"},
            {"attr1": 123, "attr2": "abc", "attr3": "def", "attr4": 987},
            {"attr2": 9.5, "attr3": "xyz"},
            "200",
        ],
    ],
    [
        """
state.set("pyscript.var1", 100, attr1=1, attr2=3.5)
s = pyscript.var1
chk = [[s, s.attr1, s.attr2]]
s.attr2 += 6.5
chk.append([s, s.attr1, s.attr2])
s.attr3 = 100
chk.append([s, s.attr1, s.attr2, s.attr3])
pyscript.var1 = 0
pyscript.var1 = s
chk.append([pyscript.var1, pyscript.var1.attr1, pyscript.var1.attr2, pyscript.var1.attr3])
chk
""",
        [["100", 1, 3.5], ["100", 1, 10], ["100", 1, 10, 100], ["100", 1, 10, 100]],
    ],
    [
        """
state.set("pyscript.var1", 100, attr1=1, attr2=3.5)
[state.exist("pyscript.var1"), state.exist("pyscript.varDoesntExist"), state.exist("pyscript.var1.attr1"),
    state.exist("pyscript.var1.attrDoesntExist"), state.exist("pyscript.varDoesntExist.attrDoesntExist")]
""",
        [True, False, True, False, False],
    ],
    ["eval('1+2')", 3],
    ["x = 5; eval('2 * x')", 10],
    ["x = 5; exec('x = 2 * x'); x", 10],
    [
        """
def f(x, /, y = 5):
    return x + y
[f(2), f(2, 2), f(2, y=3)]
""",
        [7, 4, 5],
    ],
    [
        """
def func():
    x = 5
    exec('x = 2 * x')
    return x
func()
""",
        5,
    ],
    ["x = 5; locals()['x'] = 10; x", 10],
    ["x = 5; globals()['x'] = 10; x", 10],
    [
        """
def func():
    x = 5
    locals()['x'] = 10
    return x
func()
""",
        5,
    ],
    [
        """
bar = 100
bar2 = 50
bar3 = [0]
def func(bar=6):
    def foo(bar=6):
        bar += 2
        bar5 = 100
        exec("bar2 += 1; bar += 10; bar3[0] = 1234 + bar + bar2; bar4 = 123; bar5 += 10")
        bar += 2
        del bar5
        return [bar, bar2, bar3, eval('bar2'), eval('bar4'), locals()]
    return foo(bar)
[func(), func(5), bar, bar2]
    """,
        [
            [10, 50, [1302], 51, 123, {"bar": 10, "bar2": 51, "bar4": 123}],
            [9, 50, [1302], 51, 123, {"bar": 9, "bar2": 51, "bar4": 123}],
            100,
            50,
        ],
    ],
    [
        """
bar = 100
bar2 = 50
bar3 = [0]
def func(bar=6):
    bar2 = 10
    def foo(bar=6):
        nonlocal bar2
        bar += 2
        exec("bar2 += 1; bar += 10; bar3[0] = 1234 + bar2")
        return [bar, bar2, bar3, eval('bar2'), locals()]
    return foo(bar)
[func(), func(5), bar, bar2]
""",
        [[8, 10, [1245], 10, {"bar": 8, "bar2": 10}], [7, 10, [1245], 10, {"bar": 7, "bar2": 10}], 100, 50],
    ],
    [
        """
x = 10
def foo():
    x = 5
    del x
    return eval("x")
foo()
""",
        10,
    ],
    [
        """
bar = 100
bar2 = 50
bar3 = [0]
def func(bar=6):
    bar2 = 10
    def foo(bar=6):
        nonlocal bar2
        bar += 2
        del bar
        exec("bar2 += 1; bar += 10; bar3[0] = 1234 + bar2 + bar")
        return [bar3, eval('bar'), eval('bar2'), locals()]
    del bar2
    return foo(bar)
[func(), func(5), bar, bar2]
""",
        [[[1395], 100, 50, {}], [[1395], 100, 50, {}], 100, 50],
    ],
    [
        """
value = 10
def inner():
    return value
x = inner()
x
""",
        10,
    ],
    [
        """
value = 10
if True:
    def inner():
        return value
    x = inner()
x
""",
        10,
    ],
    [
        """
value = 4
def make_func(value):
    def inner():
        return value
    return inner
make_func(10)()
""",
        10,
    ],
    [
        """
value = 4
def make_func(value):
    value2 = value
    if True:
        def inner():
            return value2
        return inner
make_func(10)()
""",
        10,
    ],
    ["eval('xyz', {'xyz': 10})", 10],
    ["g = {'xyz': 10}; eval('xyz', g, {})", 10],
    ["g = {'xyz': 10}; eval('xyz', {}, g)", 10],
    ["g = {'xyz': 10}; exec('xyz = 20', {}, g); g", {"xyz": 20}],
    ["g = {'xyz': 10}; xyz = 'abc'; exec('xyz = 20', g, {}); [g['xyz'], xyz]", [10, "abc"]],
    ["g = {'xyz': 10}; exec('xyz = 20', {}, g); g", {"xyz": 20}],
    ["x = 18; locals()['x']", 18],
    ["import math; globals()['math'].sqrt(1024)", 32],
    ["import math; exec('xyz = math.floor(5.6)'); xyz", 5],
    ["import random as rand, math as m\n[rand.uniform(10,10), m.sqrt(1024)]", [10, 32]],
    ["import cmath\ncmath.sqrt(complex(3, 4))", 2 + 1j],
    ["from math import sqrt as sqroot\nsqroot(1024)", 32],
    ["from math import sin, cos, sqrt\nsqrt(1024)", 32],
    ["from math import *\nsqrt(1024)", 32],
    ["from math import sin, floor, sqrt; [sqrt(9), floor(10.5)]", [3, 10]],
    ["from math import *; [sqrt(9), floor(10.5)]", [3, 10]],
    ["from math import floor as floor_alt, sqrt as sqrt_alt; [sqrt_alt(9), floor_alt(10.5)]", [3, 10]],
    ["task.executor(sum, range(5))", 10],
    ["task.executor(int, 'ff', base=16)", 255],
    ["[i for i in range(7) if i != 5 if i != 3]", [0, 1, 2, 4, 6]],
    [
        "i = 100; k = 10; [[k * i for i in range(3) for k in range(5)], i, k]",
        [[0, 0, 0, 0, 0, 0, 1, 2, 3, 4, 0, 2, 4, 6, 8], 100, 10],
    ],
    [
        "i = 100; k = 10; [[[k * i for i in range(3)] for k in range(5)], i, k]",
        [[[0, 0, 0], [0, 1, 2], [0, 2, 4], [0, 3, 6], [0, 4, 8]], 100, 10],
    ],
    [
        "i = 100; k = 10; [{k * i for i in range(3) for k in range(5)}, i, k]",
        [{0, 1, 2, 3, 4, 6, 8}, 100, 10],
    ],
    [
        "i = 100; k = 10; [[{k * i for i in range(3)} for k in range(5)], i, k]",
        [[{0}, {0, 1, 2}, {0, 2, 4}, {0, 3, 6}, {0, 4, 8}], 100, 10],
    ],
    ["i = [10]; [[i[0] for i[0] in range(5)], i]", [[0, 1, 2, 3, 4], [4]]],
    ["i = [10]; [{i[0] for i[0] in range(5)}, i]", [{0, 1, 2, 3, 4}, [4]]],
    [
        """
matrix = [[1, 2, 3], [4, 5], [6, 7, 8, 9]]
[val for sublist in matrix for val in sublist if val != 8]
""",
        [1, 2, 3, 4, 5, 6, 7, 9],
    ],
    [
        """
matrix = [[1, 2, 3], [4, 5], [6, 7, 8, 9]]
[val for sublist in matrix if sublist[0] != 4 for val in sublist if val != 8]
""",
        [1, 2, 3, 6, 7, 9],
    ],
    [
        """
# check short-circuit of nested if
cnt = 0
def no_op(i):
    global cnt
    cnt += 1
    return i
[i for i in range(7) if no_op(i) != 5 if no_op(i) != 3] + [cnt]
""",
        [0, 1, 2, 4, 6, 13],
    ],
    ["{i for i in range(7) if i != 5 if i != 3}", {0, 1, 2, 4, 6}],
    [
        """
matrix = [[1, 2, 3], [4, 5], [6, 7, 8, 9]]
{val for sublist in matrix for val in sublist if val != 8}
""",
        {1, 2, 3, 4, 5, 6, 7, 9},
    ],
    [
        """
matrix = [[1, 2, 3], [4, 5], [6, 7, 8, 9]]
{val for sublist in matrix if sublist[0] != 4 for val in sublist if val != 8}
""",
        {1, 2, 3, 6, 7, 9},
    ],
    [
        """
# check short-circuit of nested if
cnt = 0
def no_op(i):
    global cnt
    cnt += 1
    return i
[{i for i in range(7) if no_op(i) != 5 if no_op(i) != 3}, cnt]
""",
        [{0, 1, 2, 4, 6}, 13],
    ],
    ["{str(i):i for i in range(5) if i != 3}", {"0": 0, "1": 1, "2": 2, "4": 4}],
    ["i = 100; [{str(i):i for i in range(5) if i != 3}, i]", [{"0": 0, "1": 1, "2": 2, "4": 4}, 100]],
    ["{v:k for k,v in {str(i):i for i in range(5)}.items()}", {0: "0", 1: "1", 2: "2", 3: "3", 4: "4"}],
    [
        "{f'{i}+{k}':i+k for i in range(3) for k in range(3)}",
        {"0+0": 0, "0+1": 1, "0+2": 2, "1+0": 1, "1+1": 2, "1+2": 3, "2+0": 2, "2+1": 3, "2+2": 4},
    ],
    [
        "{f'{i}+{k}':i+k for i in range(3) for k in range(3) if k <= i}",
        {"0+0": 0, "1+0": 1, "1+1": 2, "2+0": 2, "2+1": 3, "2+2": 4},
    ],
    ["def f(l=5): return [i for i in range(l)];\nf(6)", [0, 1, 2, 3, 4, 5]],
    ["def f(l=5): return {i+j for i,j in zip(range(l), range(l))};\nf()", {0, 2, 4, 6, 8}],
    [
        "def f(l=5): return {i:j for i,j in zip(range(l), range(1,l+1))};\nf()",
        {0: 1, 1: 2, 2: 3, 3: 4, 4: 5},
    ],
    [
        """
d = {"x": 1, "y": 2, "z": 3}
s = []
for k, v in d.items():
    s.append(f"{k}: {v}")
s
""",
        ["x: 1", "y: 2", "z: 3"],
    ],
    [
        """
d = {"x": 1, "y": 2, "z": 3}
i = 0
s = []
k = [0, 0, 0]
for k[i], v in d.items():
    s.append([k.copy(), v])
    i += 1
s
""",
        [[["x", 0, 0], 1], [["x", "y", 0], 2], [["x", "y", "z"], 3]],
    ],
    [
        """
def foo(bar=6):
    if bar == 5:
        return
    else:
        return 2 * bar
[foo(), foo(5), foo('xxx')]
""",
        [12, None, "xxxxxx"],
    ],
    [
        """
bar = 100
def foo(bar=6):
    bar += 2
    return eval('bar')
    bar += 5
    return 1000
[foo(), foo(5), bar]
""",
        [8, 7, 100],
    ],
    [
        """
bar = 100
def foo(bar=6):
   bar += 2
   del bar
   return eval('bar')
   bar += 5
   return 1000
[foo(), foo(5), bar]
""",
        [100, 100, 100],
    ],
    [
        """
bar = 100
bar2 = 1000
bar3 = 100
def foo(arg=6):
    global bar, bar2, bar3
    bar += arg
    bar2 = 1001
    del bar3
    return bar
    bar += arg
    return 1000
[foo(), foo(5), bar, bar2]
""",
        [106, 111, 111, 1001],
    ],
    [
        """
def func():
    global x
    x = 134
func()
x
""",
        134,
    ],
    [
        """
def foo0(arg=6):
    bar = 100
    bar2 = 1000
    bar3 = 100
    def foo1(arg=6):
        nonlocal bar, bar2, bar3
        bar += arg
        bar2 = 1001
        del bar3
        return bar
        bar += arg
        return 1000
    return [foo1(arg), bar, bar2]
[foo0(), foo0(5)]
""",
        [[106, 106, 1001], [105, 105, 1001]],
    ],
    [
        """
bar = 50
bar2 = 500
bar3 = 50
def foo0(arg=6):
    bar = 100
    bar2 = 1000
    bar3 = 100
    def foo1(arg=6):
        nonlocal bar, bar2, bar3
        bar += arg
        bar2 = 1001
        del bar3
        return eval('bar')
        bar += arg
        return 1000
    return [foo1(arg), bar, eval('bar2')]
[foo0(), foo0(5)]
""",
        [[106, 106, 1001], [105, 105, 1001]],
    ],
    [
        """
bar = 50
bar2 = 500
bar3 = 50
def foo0(arg=6):
    bar = 100
    bar2 = 1000
    bar3 = 100
    def foo1(arg=6):
        nonlocal bar, bar2, bar3
        bar += arg
        bar2 = 1001
        del bar3
        return eval('bar')
        bar += arg
        return 1000
    # on real python, eval('[foo1(arg), bar, bar2]') doesn't yield
    # the same result as our code; if we eval each entry then they
    # get the same result
    return [eval('foo1(arg)'), eval('bar'), eval('bar2')]
[foo0(), foo0(5), eval('bar'), eval('bar2')]
""",
        [[106, 106, 1001], [105, 105, 1001], 50, 500],
    ],
    [
        """
def f():
    def b():
        return s+g
    s = "hello "
    return b
b = f()
g = "world"
b()
""",
        "hello world",
    ],
    [
        """
def foo(cnt=4):
    sum = 0
    for i in range(cnt):
        sum += i
        if i == 6:
            return 1000 + sum
        if i == 7:
            break
    return sum
[foo(3), foo(6), foo(10), foo(20), foo()]
""",
        [sum(range(3)), sum(range(6)), 1000 + sum(range(7)), 1000 + sum(range(7)), sum(range(4))],
    ],
    [
        """
def foo(cnt=5):
    sum = 0
    for i in range(cnt):
        if i == 4:
            continue
        if i == 8:
            break
        sum += i
    return sum
[foo(3), foo(6), foo(10), foo(20), foo()]
""",
        [sum(range(3)), sum(range(6)) - 4, sum(range(9)) - 4 - 8, sum(range(9)) - 4 - 8, sum(range(5)) - 4],
    ],
    [
        """
def foo(cnt=5):
    sum = 0
    for i in range(cnt):
        if i == 8:
            break
        sum += i
    else:
        return 1000 + sum
    return sum
[foo(3), foo(6), foo(10), foo(20), foo()]
""",
        [
            sum(range(3)) + 1000,
            sum(range(6)) + 1000,
            sum(range(9)) - 8,
            sum(range(9)) - 8,
            sum(range(5)) + 1000,
        ],
    ],
    [
        """
def foo(cnt=5):
    sum = 0
    i = 0
    while i < cnt:
        if i == 8:
            break
        sum += i
        i += 1
    else:
        return 1000 + sum
    return sum
[foo(3), foo(6), foo(10), foo(20), foo()]
""",
        [
            sum(range(3)) + 1000,
            sum(range(6)) + 1000,
            sum(range(9)) - 8,
            sum(range(9)) - 8,
            sum(range(5)) + 1000,
        ],
    ],
    [
        """
def foo(cnt):
    sum = 0
    for i in range(cnt):
        sum += i
        if i != 6:
            pass
        else:
            return 1000 + sum
        if i == 7:
            break
    return sum
[foo(3), foo(6), foo(10), foo(20)]
""",
        [sum(range(3)), sum(range(6)), 1000 + sum(range(7)), 1000 + sum(range(7))],
    ],
    [
        """
def foo(cnt):
    sum = 0
    i = 0
    while i < cnt:
        sum += i
        if i != 6:
            pass
        else:
            return 1000 + sum
        if i == 7:
            break
        i += 1
    return sum
[foo(3), foo(6), foo(10), foo(20)]
""",
        [sum(range(3)), sum(range(6)), 1000 + sum(range(7)), 1000 + sum(range(7))],
    ],
    [
        """
def f1(x, y, z):
    def f2():
        y = 5
        def f3():
            nonlocal x, y
            x += 1
            y += 1
            return x + y + z
        return f3()
    return [x, y, z, f2()]
f1(10, 20, 30)
""",
        [10, 20, 30, 47],
    ],
    [
        """
def twice(func):
    def twice_func(*args, **kwargs):
        func(*args, **kwargs)
        return func(*args, **kwargs)
    return twice_func

val = 0
val_list = []

@twice
def foo1():
    global val, val_list
    val_list.append(val)
    val += 1

@twice
@twice
@twice
def foo2():
    global val, val_list
    val_list.append(val)
    val += 1

foo1()
foo2()
val_list
""",
        list(range(0, 10)),
    ],
    [
        """
@pyscript_compile
def twice(func):
    def twice_func(*args, **kwargs):
        func(*args, **kwargs)
        return func(*args, **kwargs)
    return twice_func

val = 0
val_list = []

@pyscript_compile
@twice
def foo1():
    global val, val_list
    val_list.append(val)
    val += 1

@pyscript_compile
@twice
@twice
@twice
def foo2():
    global val, val_list
    val_list.append(val)
    val += 1

foo1()
foo2()
val_list
""",
        list(range(0, 10)),
    ],
    [
        """
def test_func(arg, kw=0):
    return [arg, kw]

def func_factory(func_handle,*args,**kwargs):
    def func_trig():
        return func_handle(*args,**kwargs)
    return func_trig

func_factory(test_func, "my_arg", kw=10)()
""",
        ["my_arg", 10],
    ],
    [
        """
import threading

# will run in the same thread, and return a different thread ident
@pyscript_compile()
def func1():
    return threading.get_ident()

# will run in a different thread, and return a different thread ident
@pyscript_compile()
def func2():
    return threading.get_ident()

[threading.get_ident() == func1(), threading.get_ident() != func2()]

[True, True]
""",
        [True, True],
    ],
    [
        """
def twice(func):
    def twice_func(*args, **kwargs):
        func(*args, **kwargs)
        return func(*args, **kwargs)
    return twice_func

def repeat(num_times):
    def decorator_repeat(func):
        def wrapper_repeat(*args, **kwargs):
            for _ in range(num_times):
                value = func(*args, **kwargs)
            return value
        return wrapper_repeat
    return decorator_repeat

val = 0
val_list = []

@twice
@repeat(3)
def foo1():
    global val, val_list
    val_list.append(val)
    val += 1

@repeat(3)
@twice
def foo2():
    global val, val_list
    val_list.append(val)
    val += 1

foo1()
foo2()
val_list
""",
        list(range(0, 12)),
    ],
    [
        """
def repeat(num_times):
    def decorator_repeat(func):
        def wrapper_repeat(*args, **kwargs):
            for _ in range(num_times):
                value = func(*args, **kwargs)
            return value
        return wrapper_repeat
    return decorator_repeat

def repeat2(num_times):
    def decorator_repeat(func):
        nonlocal num_times
        def wrapper_repeat(*args, **kwargs):
            for _ in range(num_times):
                value = func(*args, **kwargs)
            return value
        return wrapper_repeat
    return decorator_repeat

x = 0
def func(incr):
    global x
    x += incr
    return x

[repeat(3)(func)(10), repeat2(3)(func)(20)]
""",
        [30, 90],
    ],
    [
        """
def f1(value):
    i = 1
    def f2(value):
        i = 2
        def f3(value):
            return value + i
        return f3(2 * value)
    return f2(2 * value)
f1(10)
""",
        42,
    ],
    [
        """
def f1(value):
    i = 1
    def f2(value):
        i = 2
        def f3():
            return value + i
        return f3()
    return f2(2 * value)
f1(10)
""",
        22,
    ],
    [
        """
def foo():
    global f_bar
    def f_bar():
        return "hello"

foo()
f_bar()
""",
        "hello",
    ],
    [
        """
def foo():
    global inner_class
    class inner_class:
        def method1():
            return 1234

foo()
inner_class.method1()
""",
        1234,
    ],
    [
        """
def foo(x=30, *args, y = 123, **kwargs):
    return [x, y, args, kwargs]
[foo(a = 10, b = 3), foo(40, 7, 8, 9, a = 10, y = 3), foo(x=42)]
""",
        [[30, 123, (), {"a": 10, "b": 3}], [40, 3, (7, 8, 9), {"a": 10}], [42, 123, (), {}]],
    ],
    [
        """
def foo(*args):
    return [*args]
lst = [6, 10]
[foo(2, 3, 10) + [*lst], [foo(*lst), *lst]]
""",
        [[2, 3, 10, 6, 10], [[6, 10], 6, 10]],
    ],
    [
        """
def foo(arg1=None, **kwargs):
    return [arg1, kwargs]
[foo(), foo(arg1=1), foo(arg2=20), foo(arg1=10, arg2=20), foo(**{'arg2': 30})]
""",
        [[None, {}], [1, {}], [None, {"arg2": 20}], [10, {"arg2": 20}], [None, {"arg2": 30}]],
    ],
    [
        """
def func(exc):
    try:
        x = 1
        if exc:
            raise exc
    except NameError as err:
        x += 100
    except (NameError, OSError) as err:
        x += 200
    except Exception as err:
        x += 300
        return x
    else:
        x += 10
    finally:
        x += 2
    x += 1
    return x
[func(None), func(NameError("x")), func(OSError("x")), func(ValueError("x"))]
""",
        [14, 104, 204, 301],
    ],
    [
        """
def func(exc):
    try:
        x = 1
        if exc:
            raise exc
    except NameError as err:
        x += 100
    except (NameError, OSError) as err:
        x += 200
    except Exception as err:
        x += 300
        return x
    else:
        return x + 10
    finally:
        x += 2
        return x
    x += 1
    return x
[func(None), func(NameError("x")), func(OSError("x")), func(ValueError("x"))]
""",
        [3, 103, 203, 303],
    ],
    [
        """
class Test:
    x = 10
    def __init__(self, value):
        self.y = value

    def set_x(self, value):
        Test.x += 2
        self.x = value

    def set_y(self, value):
        self.y = value

    def get(self):
        return [self.x, self.y]

t1 = Test(20)
t2 = Test(40)
Test.x = 5
[t1.get(), t2.get(), t1.set_x(100), t1.get(), t2.get(), Test.x]
""",
        [[5, 20], [5, 40], None, [100, 20], [7, 40], 7],
    ],
    [
        """
i = 10
def f():
    return i
i = 42
f()
""",
        42,
    ],
    [
        """
class Ctx:
    def __init__(self, msgs, val):
        self.val = val
        self.msgs = msgs

    def get(self):
        return self.val

    def __enter__(self):
        self.msgs.append(f"__enter__ {self.val}")
        return self

    def __exit__(self, type, value, traceback):
        self.msgs.append(f"__exit__ {self.val}")

msgs = []
x = [None, None]

with Ctx(msgs, 5) as x[0], Ctx(msgs, 10) as x[1]:
    msgs.append(x[0].get())
    msgs.append(x[1].get())

try:
    with Ctx(msgs, 5) as x[0], Ctx(msgs, 10) as x[1]:
        msgs.append(x[0].get())
        msgs.append(x[1].get())
        1/0
except Exception as exc:
    msgs.append(f"got {exc}")

for i in range(5):
    with Ctx(msgs, 5 + i) as x[0], Ctx(msgs, 10 + i) as x[1]:
        msgs.append(x[0].get())
        if i == 0:
            continue
        msgs.append(x[1].get())
        if i >= 1:
            break

def func(i):
    x = [None, None]
    with Ctx(msgs, 5 + i) as x[0], Ctx(msgs, 10 + i) as x[1]:
        msgs.append(x[0].get())
        if i == 1:
            return x[1].get()
        msgs.append(x[1].get())
        if i == 2:
            return x[1].get() * 2
    return 0
msgs.append(func(0))
msgs.append(func(1))
msgs.append(func(2))
msgs
""",
        [
            "__enter__ 5",
            "__enter__ 10",
            5,
            10,
            "__exit__ 10",
            "__exit__ 5",
            "__enter__ 5",
            "__enter__ 10",
            5,
            10,
            "__exit__ 10",
            "__exit__ 5",
            "got division by zero",
            "__enter__ 5",
            "__enter__ 10",
            5,
            "__exit__ 10",
            "__exit__ 5",
            "__enter__ 6",
            "__enter__ 11",
            6,
            11,
            "__exit__ 11",
            "__exit__ 6",
            "__enter__ 5",
            "__enter__ 10",
            5,
            10,
            "__exit__ 10",
            "__exit__ 5",
            0,
            "__enter__ 6",
            "__enter__ 11",
            6,
            "__exit__ 11",
            "__exit__ 6",
            11,
            "__enter__ 7",
            "__enter__ 12",
            7,
            12,
            "__exit__ 12",
            "__exit__ 7",
            24,
        ],
    ],
    [
        """
class Test:
    x = 10
    def __init__(self, value):
        self.y = value

    def set_x(self, value):
        Test.x += 2
        self.x = value

@pyscript_compile
async def f3(t, val):
    await t.set_x(val)
    return [t.x, t.y, Test.x]

t = Test(25)
f3(t, 50)
""",
        [50, 25, 12],
    ],
    [
        """
def func(arg, mul=1):
    return mul * arg

@pyscript_compile
async def f2(arg, mul=1):
    return await func(arg, mul=mul)

[f2(10), f2(10, mul=2)]
""",
        [10, 20],
    ],
    [
        """
x = []
class TestClass:
    def __init__(self):
        x.append(1)

    @pyscript_compile
    def incr(self, a):
        x.append(a)
        return a + 1

    def incr2(self, a):
        x.append(a)
        return a + 1

    @pyscript_compile
    def __del__(self):
        x.append(-1)

c = TestClass()
c.incr(10)
c.incr2(20)
del c
x
""",
        [1, 10, 20, -1],
    ],
    [
        """
def func1(m):
    def func2():
        m[0] += 1
        return m[0]

    def func3():
        n[0] += 10
        return n[0]

    n = m
    return func2, func3

f2, f3 = func1([10])
[f2(), f3(), f2(), f3(), f2(), f3()]
""",
        [11, 21, 22, 32, 33, 43],
    ],
    [
        """
def func1(m=0):
    def func2():
        nonlocal m
        m += 1
        return eval("m")

    def func3():
        nonlocal n
        n += 10
        return n

    n = m
    return func2, func3

f2, f3 = func1(10)
f4, f5 = func1(50)
[f2(), f3(), f4(), f5(), f2(), f3(), f4(), f5(), f2(), f3(), f4(), f5()]
""",
        [11, 20, 51, 60, 12, 30, 52, 70, 13, 40, 53, 80],
    ],
    [
        """
def func():
    k = 10
    def f1():
        nonlocal i
        i += 2
        return i
    def f2():
        nonlocal k
        k += 5
        return k
    i = 40
    k += 5
    return f1, f2
f1, f2 = func()
[f1(), f2(), f1(), f2(), f1(), f2()]
""",
        [42, 20, 44, 25, 46, 30],
    ],
    [
        """
def f1():
    return 100

def func():
    def f2(x):
        nonlocal y
        y += f1(2*x)
        return y

    def f1(x):
        nonlocal y
        y += x
        return y

    y = 1
    return f1, f2

f3, f4 = func()
[f3(2), f4(3), f3(4), f4(5)]
""",
        [3, 12, 16, 42],
    ],
    [
        """
def func():
    def f1():
        nonlocal i
        del i
        i = 20
        return i + 1

    def f2():
        return 2 * i

    i = 10
    return f1, f2

f1, f2 = func()
[f2(), f1(), f2()]
""",
        [20, 21, 40],
    ],
    [
        """
def func():
    def func2():
        z = x + 2
        return z
    x = 1
    return func2()
func()
""",
        3,
    ],
    [
        """
def func(arg):
    task.unique("func")
    task.sleep(10000)

id = task.create(func, 19)
done, pending = task.wait({id}, timeout=0)
res = [len(done), len(pending), id in pending, task.name2id("func") == id, task.name2id()["func"] == id]
task.cancel(id)
res
""",
        [0, 1, True, True, True],
    ],
    [
        """
def foo():
    def bar():
        result = []
        result.append("bar")
        other = 1
        return result

    result = bar()
    return result

foo()
""",
        ["bar"],
    ],
    [
        """
async def func():
    return 42

await func()
""",
        42,
    ],
    [
        """
import asyncio
async def coro():
    await asyncio.sleep(1e-5)
    return "done"

await coro()
""",
        "done",
    ],
    [
        """
import asyncio

@pyscript_compile
async def nested():
    await asyncio.sleep(1e-8)
    return 42

@pyscript_compile
async def run():
    task = asyncio.create_task(nested())

    # "task" can now be used to cancel "nested()", or
    # can simply be awaited to wait until it is complete:
    await task
    return "done"

await run()
""",
        "done",
    ],
    [
        """
class Test:
    def __init__(self, value):
        self.val = value

    async def getval(self):
        async def handler():
            return self.val
        return handler()

t = Test(20)
[await(await t.getval()), t.getval()]
""",
        [20, 20],
    ],
    [
        """
import asyncio

future = asyncio.Future()
future.set_result(True)
await future
""",
        True,
    ],
    [
        """
@pyscript_compile
async def coro0():
    return 123

async def coro1():
    return 456

[await coro0(), await coro1()]
""",
        [123, 456],
    ],
]


async def run_one_test(test_data):
    """Run one interpreter test."""
    source, expect = test_data
    global_ctx = GlobalContext("test", global_sym_table={}, manager=GlobalContextMgr)
    ast = AstEval("test", global_ctx=global_ctx)
    Function.install_ast_funcs(ast)
    ast.parse(source)
    if ast.get_exception() is not None:
        print(f"Parsing {source} failed: {ast.get_exception()}")
    # print(ast.dump())
    result = await ast.eval({"sym_local": 10}, merge_local=True)
    assert result == expect


@pytest.mark.asyncio
async def test_eval(hass):
    """Test interpreter."""
    hass.data[DOMAIN] = {CONFIG_ENTRY: MockConfigEntry(domain=DOMAIN, data={CONF_ALLOW_ALL_IMPORTS: True})}
    Function.init(hass)
    State.init(hass)
    State.register_functions()
    TrigTime.init(hass)

    for test_data in evalTests:
        await run_one_test(test_data)
    await Function.waiter_sync()
    await Function.waiter_stop()
    await Function.reaper_stop()


evalTestsExceptions = [
    [None, "parsing error compile() arg 1 must be a string, bytes or AST object"],
    ["1+", "syntax error invalid syntax (test, line 1)"],
    ["1+'x'", "Exception in test line 1 column 2: unsupported operand type(s) for +: 'int' and 'str'"],
    ["xx", "Exception in test line 1 column 0: name 'xx' is not defined"],
    ["(x, y) = (1, 2, 4)", "Exception in test line 1 column 16: too many values to unpack (expected 2)"],
    [
        "(x, y) = iter([1, 2, 4])",
        "Exception in test line 1 column 21: too many values to unpack (expected 2)",
    ],
    ["(x, y, z) = (1, 2)", "Exception in test line 1 column 16: too few values to unpack (expected 3)"],
    [
        "(x, y, z) = iter([1, 2])",
        "Exception in test line 1 column 21: too few values to unpack (expected 3)",
    ],
    ["(x, y) = 1", "Exception in test line 1 column 9: cannot unpack non-iterable object"],
    ["x: int; x", "Exception in test line 1 column 8: name 'x' is not defined"],
    [
        "a, *y, w, z = range(2)",
        "Exception in test line 1 column 20: too few values to unpack (expected at least 3)",
    ],
    ["assert 1 == 0, 'this is an error'", "Exception in test line 1 column 15: this is an error"],
    ["assert 1 == 0", "Exception in test line 1 column 12: "],
    ["pyscript.var1.attr1 = 10", "Exception in test line 1 column 0: state pyscript.var1 doesn't exist"],
    [
        "import math; math.sinXYZ",
        "Exception in test line 1 column 13: module 'math' has no attribute 'sinXYZ'",
    ],
    ["f'xxx{'", "syntax error f-string: expecting '}' (test, line 1)"],
    [
        "f'xxx{foo() i}'",
        {
            "syntax error invalid syntax (<fstring>, line 1)",  # < 3.9
            "syntax error f-string: invalid syntax (test, line 1)",  # >= 3.9
            "syntax error f-string: invalid syntax. Perhaps you forgot a comma? (test, line 1)",  # >= 3.10
            "syntax error invalid syntax. Perhaps you forgot a comma? (test, line 1)",  # >= 3.12
        },
    ],
    ["del xx", "Exception in test line 1 column 0: name 'xx' is not defined"],
    [
        "pyscript.var1 = 1; del pyscript.var1; pyscript.var1",
        "Exception in test line 1 column 38: name 'pyscript.var1' is not defined",
    ],
    [
        "pyscript.var1 = 1; state.delete('pyscript.var1'); pyscript.var1",
        "Exception in test line 1 column 50: name 'pyscript.var1' is not defined",
    ],
    ["return", "Exception in test line 1 column 0: return statement outside function"],
    ["break", "Exception in test line 1 column 0: break statement outside loop"],
    ["continue", "Exception in test line 1 column 0: continue statement outside loop"],
    ["raise", "Exception in test line 1 column 0: No active exception to reraise"],
    ["yield", "Exception in test line 1 column 0: test: not implemented ast ast_yield"],
    ["task.executor(5)", "Exception in test line 1 column 14: function 5 is not callable by task.executor"],
    [
        "task.executor(task.sleep)",
        "Exception in test line 1 column 14: function <bound method Function.async_sleep of <class 'custom_components.pyscript.function.Function'>> is not callable by task.executor",
    ],
    ["task.name2id('notask')", "Exception in test line 1 column 13: task name 'notask' is unknown"],
    [
        "state.get('pyscript.xyz1.abc')",
        "Exception in test line 1 column 10: name 'pyscript.xyz1' is not defined",
    ],
    [
        "pyscript.xyz1 = 1; state.get('pyscript.xyz1.abc')",
        "Exception in test line 1 column 29: state 'pyscript.xyz1' has no attribute 'abc'",
    ],
    [
        "import cmath; exec('xyz = cmath.sqrt(complex(3, 4))', {})",
        "Exception in test line 1 column 54: Exception in exec() line 1 column 6: name 'cmath.sqrt' is not defined",
    ],
    ["func1(1)", "Exception in test line 1 column 0: name 'func1' is not defined"],
    [
        "def func(a):\n    pass\nfunc()",
        "Exception in test line 3 column 0: func() missing 1 required positional arguments",
    ],
    [
        "def func(a):\n    pass\nfunc(1, 2)",
        "Exception in test line 3 column 8: func() called with too many positional arguments",
    ],
    [
        "def func(a=1):\n    pass\nfunc(1, a=3)",
        "Exception in test line 3 column 5: func() got multiple values for argument 'a'",
    ],
    [
        "def func(*a, b):\n    pass\nfunc(1, 2)",
        "Exception in test line 3 column 8: func() missing required keyword-only arguments",
    ],
    [
        """
def func(b=1):
    pass

func(a=2, trigger_type=1)
""",
        "Exception in test line 5 column 23: func() called with unexpected keyword arguments: a",
    ],
    [
        """
def f(x, z, /, y = 5):
    return x + y
f(x=4, z=2)
""",
        "Exception in test line 4 column 9: f() got some positional-only arguments passed as keyword arguments: 'x, z'",
    ],
    [
        """
def f(x, /, y = 5):
    return x + y
f(x=4)
""",
        "Exception in test line 4 column 4: f() got some positional-only arguments passed as keyword arguments: 'x'",
    ],
    [
        "from .xyz import abc",
        "Exception in test line 1 column 0: attempted relative import with no known parent package",
    ],
    [
        "from ...xyz import abc",
        "Exception in test line 1 column 0: attempted relative import with no known parent package",
    ],
    [
        "from . import abc",
        "Exception in test line 1 column 0: attempted relative import with no known parent package",
    ],
    ["import asyncio", "Exception in test line 1 column 0: import of asyncio not allowed"],
    ["import xyzabc123", "Exception in test line 1 column 0: import of xyzabc123 not allowed"],
    ["from asyncio import xyz", "Exception in test line 1 column 0: import from asyncio not allowed"],
    [
        """
def func():
    nonlocal x
    x = 1
func()
""",
        "Exception in test line 2 column 0: no binding for nonlocal 'x' found",
    ],
    [
        """
def func():
    nonlocal x
    x += 1
func()
""",
        "Exception in test line 2 column 0: no binding for nonlocal 'x' found",
    ],
    [
        """
def func():
    global x
    return x
func()
""",
        "Exception in func(), test line 4 column 11: global name 'x' is not defined",
    ],
    [
        """
def func():
    x = 1
    eval('1 + y')
func()
""",
        "Exception in test line 4 column 9: Exception in eval() line 1 column 4: name 'y' is not defined",
    ],
    [
        """
try:
    d = {}
    x = d["bad_key"]
except KeyError:
    raise
""",
        "Exception in test line 4 column 10: 'bad_key'",
    ],
    [
        """
x = 0
def func():
    x += 1
func()
""",
        "Exception in func(), test line 4 column 4: local variable 'x' referenced before assignment",
    ],
    [
        """
def func():
    def func2():
        nonlocal x
        del x
        del x
    x = 1
    func2()
func()
""",
        "Exception in func2(), test line 6 column 8: name 'x' is not defined",
    ],
    [
        """
def func():
    def func2():
        z = x + 2
        return z
        del x
    x = 1
    return func2()
func()
""",
        "Exception in func2(), test line 4 column 12: name 'x' is not defined",
    ],
    [
        """
@pyscript_compile(1)
def func():
    pass
""",
        "Exception in test line 3 column 0: @pyscript_compile() takes 0 positional arguments",
    ],
    [
        """
@pyscript_compile(invald_kw=True)
def func():
    pass
""",
        "Exception in test line 3 column 0: @pyscript_compile() takes no keyword arguments",
    ],
    [
        """
@pyscript_executor()
@pyscript_compile()
def func():
    pass
""",
        "Exception in test line 4 column 0: can only specify single decorator of pyscript_compile, pyscript_executor",
    ],
]


async def run_one_test_exception(test_data):
    """Run one interpreter test that generates an exception."""
    source, expect = test_data
    global_ctx = GlobalContext("test", global_sym_table={}, manager=GlobalContextMgr)
    ast = AstEval("test", global_ctx=global_ctx)
    Function.install_ast_funcs(ast)
    ast.parse(source)
    exc = ast.get_exception()
    if exc is not None:
        if type(expect) == set:
            assert exc in expect
        else:
            assert exc == expect
        return
    await ast.eval()
    exc = ast.get_exception()
    if exc is not None:
        assert exc == expect
        return
    assert False


@pytest.mark.asyncio
async def test_eval_exceptions(hass):
    """Test interpreter exceptions."""
    hass.data[DOMAIN] = {CONFIG_ENTRY: MockConfigEntry(domain=DOMAIN, data={CONF_ALLOW_ALL_IMPORTS: False})}
    Function.init(hass)
    State.init(hass)
    State.register_functions()
    TrigTime.init(hass)

    for test_data in evalTestsExceptions:
        await run_one_test_exception(test_data)
    await Function.waiter_sync()
    await Function.waiter_stop()
    await Function.reaper_stop()
