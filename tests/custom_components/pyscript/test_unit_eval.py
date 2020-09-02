"""Unit tests for Python interpreter."""
import asyncio

from config.custom_components.pyscript.eval import AstEval
from config.custom_components.pyscript.global_ctx import GlobalContext
import config.custom_components.pyscript.handler as handler
import config.custom_components.pyscript.state as state

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
    ["args = [1, 5, 10]; {6, *args, 15}", {1, 5, 6, 10, 15}],
    ["args = [1, 5, 10]; [6, *args, 15]", [6, 1, 5, 10, 15]],
    ["kw = {'x': 1, 'y': 5}; {**kw}", {"x": 1, "y": 5}],
    [
        "kw = {'x': 1, 'y': 5}; kw2 = {'z': 10}; {**kw, **kw2}",
        {"x": 1, "y": 5, "z": 10},
    ],
    ["[*iter([1, 2, 3])]", [1, 2, 3]],
    ["{*iter([1, 2, 3])}", {1, 2, 3}],
    ["if 1: x = 10\nelse: x = 20\nx", 10],
    ["if 0: x = 10\nelse: x = 20\nx", 20],
    ["i = 0\nwhile i < 5: i += 1\ni", 5],
    ["i = 0\nwhile i < 5: i += 2\ni", 6],
    ["i = 0\nwhile i < 5:\n    i += 1\n    if i == 3: break\n2 * i", 6],
    [
        "i = 0; k = 10\nwhile i < 5:\n    i += 1\n    if i <= 2: continue\n    k += 1\nk + i",
        18,
    ],
    ["i = 1; break; i = 1/0", None],
    ["s = 0;\nfor i in range(5):\n    s += i\ns", 10],
    ["s = 0;\nfor i in iter([10,20,30]):\n    s += i\ns", 60],
    [
        "z = {'foo': 'bar', 'foo2': 12}; z['foo'] = 'bar2'; z",
        {"foo": "bar2", "foo2": 12},
    ],
    ["z = {'foo': 'bar', 'foo2': 12}; z['foo'] = 'bar2'; z.keys()", {"foo", "foo2"}],
    ["z = {'foo', 'bar', 12}; z", {"foo", "bar", 12}],
    [
        "x = dict(key1 = 'value1', key2 = 'value2'); x",
        {"key1": "value1", "key2": "value2"},
    ],
    [
        "x = dict(key1 = 'value1', key2 = 'value2', key3 = 'value3'); del x['key1']; x",
        {"key2": "value2", "key3": "value3"},
    ],
    [
        "x = dict(key1 = 'value1', key2 = 'value2', key3 = 'value3'); del x[['key1', 'key2']]; x",
        {"key3": "value3"},
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
    [
        "z = [0, 1, 2, 3, 4, 5, 6, 7, 8]; z[1:5:2] = [6, 8]; z",
        [0, 6, 2, 8, 4, 5, 6, 7, 8],
    ],
    ["z = [0, 1, 2, 3, 4, 5, 6, 7, 8]; z[1:5] = [10, 11]; z", [0, 10, 11, 5, 6, 7, 8]],
    [
        "z = [0, 1, 2, 3, 4, 5, 6, 7, 8]; z[1::3] = [10, 11, 12]; z",
        [0, 10, 2, 3, 11, 5, 6, 12, 8],
    ],
    [
        "z = [0, 1, 2, 3, 4, 5, 6, 7, 8]; z[4::] = [10, 11, 12, 13]; z",
        [0, 1, 2, 3, 10, 11, 12, 13],
    ],
    [
        "z = [0, 1, 2, 3, 4, 5, 6, 7, 8]; z[4:] = [10, 11, 12, 13, 14]; z",
        [0, 1, 2, 3, 10, 11, 12, 13, 14],
    ],
    [
        "z = [0, 1, 2, 3, 4, 5, 6, 7, 8]; z[:6:2] = [10, 11, 12]; z",
        [10, 1, 11, 3, 12, 5, 6, 7, 8],
    ],
    [
        "z = [0, 1, 2, 3, 4, 5, 6, 7, 8]; z[:4:] = [10, 11, 12, 13]; z",
        [10, 11, 12, 13, 4, 5, 6, 7, 8],
    ],
    [
        "z = [0, 1, 2, 3, 4, 5, 6, 7, 8]; z[::2] = [10, 11, 12, 13, 14]; z",
        [10, 1, 11, 3, 12, 5, 13, 7, 14],
    ],
    [
        "z = [0, 1, 2, 3, 4, 5, 6, 7, 8]; z[::] = [10, 11, 12, 13, 14, 15, 16, 17]; z",
        [10, 11, 12, 13, 14, 15, 16, 17],
    ],
    ["(x, y) = (1, 2); [x, y]", [1, 2]],
    ["y = [1,2]; (x, y[0]) = (3, 4); [x, y]", [3, [4, 2]]],
    ["((x, y), (z, t)) = ((1, 2), (3, 4)); [x, y, z, t]", [1, 2, 3, 4]],
    [
        "z = [1,2,3]; ((x, y), (z[2], t)) = ((1, 2), (20, 4)); [x, y, z, t]",
        [1, 2, [1, 2, 20], 4],
    ],
    ["Foo = type('Foo', (), {'x': 100}); Foo.x = 10; Foo.x", 10],
    ["Foo = type('Foo', (), {'x': 100}); Foo.x += 10; Foo.x", 110],
    ["Foo = [type('Foo', (), {'x': 100})]; Foo[0].x = 10; Foo[0].x", 10],
    [
        "Foo = [type('Foo', (), {'x': [100, 101]})]; Foo[0].x[1] = 10; Foo[0].x",
        [100, 10],
    ],
    [
        "Foo = [type('Foo', (), {'x': [0, [[100, 101]]]})]; Foo[0].x[1][0][1] = 10; Foo[0].x[1]",
        [[100, 10]],
    ],
    [
        "Foo = [type('Foo', (), {'x': [0, [[100, 101, 102, 103]]]})]; Foo[0].x[1][0][1:2] = [11, 12]; Foo[0].x[1]",
        [[100, 11, 12, 102, 103]],
    ],
    ["eval('1+2')", 3],
    ["x = 5; eval('2 * x')", 10],
    ["x = 5; exec('x = 2 * x'); x", 10],
    ["eval('xyz', {'xyz': 10})", 10],
    ["g = {'xyz': 10}; eval('xyz', g, {})", 10],
    ["g = {'xyz': 10}; eval('xyz', {}, g)", 10],
    ["g = {'xyz': 10}; exec('xyz = 20', {}, g); g", {"xyz": 20}],
    [
        "g = {'xyz': 10}; xyz = 'abc'; exec('xyz = 20', g, {}); [g['xyz'], xyz]",
        [10, "abc"],
    ],
    ["g = {'xyz': 10}; exec('xyz = 20', {}, g); g", {"xyz": 20}],
    ["x = 18; locals()['x']", 18],
    ["import math; globals()['math'].sqrt(1024)", 32],
    ["import math; exec('xyz = math.floor(5.6)'); xyz", 5],
    ["import random as rand, math as m\n[rand.uniform(10,10), m.sqrt(1024)]", [10, 32]],
    ["import cmath\ncmath.sqrt(complex(3, 4))", 2 + 1j],
    ["from math import sqrt as sqroot\nsqroot(1024)", 32],
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
@dec_test("abc")
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
        [
            sum(range(3)),
            sum(range(6)),
            1000 + sum(range(7)),
            1000 + sum(range(7)),
            sum(range(4)),
        ],
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
        [
            sum(range(3)),
            sum(range(6)) - 4,
            sum(range(9)) - 4 - 8,
            sum(range(9)) - 4 - 8,
            sum(range(5)) - 4,
        ],
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
def foo(x=30, *args, y = 123, **kwargs):
    return [x, y, args, kwargs]
[foo(a = 10, b = 3), foo(40, 7, 8, 9, a = 10, y = 3), foo(x=42)]
""",
        [
            [30, 123, (), {"a": 10, "b": 3}],
            [40, 3, (7, 8, 9), {"a": 10}],
            [42, 123, (), {}],
        ],
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
        [
            [None, {}],
            [1, {}],
            [None, {"arg2": 20}],
            [10, {"arg2": 20}],
            [None, {"arg2": 30}],
        ],
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
]


async def run_one_test(test_data, state_func, handler_func):
    """Run one interpreter test."""
    source, expect = test_data
    global_ctx = GlobalContext(
        "test",
        None,
        global_sym_table={},
        state_func=state_func,
        handler_func=handler_func,
    )
    ast = AstEval(
        "test", global_ctx=global_ctx, state_func=state_func, handler_func=handler_func
    )
    ast.parse(source)
    if ast.get_exception() is not None:
        print(f"Parsing {source} failed: {ast.get_exception()}")
    # print(ast.dump())
    result = await ast.eval({"sym_local": 10})
    assert result == expect


def test_eval(hass):
    """Test interpreter."""
    handler_func = handler.Handler(hass)
    state_func = state.State(hass, handler_func)
    state_func.register_functions()

    for test_data in evalTests:
        asyncio.run(run_one_test(test_data, state_func, handler_func))


evalTestsExceptions = [
    [None, "parsing error compile() arg 1 must be a string, bytes or AST object"],
    ["1+", "syntax error invalid syntax (test, line 1)"],
    [
        "1+'x'",
        "Exception in test line 1 column 2: unsupported operand type(s) for +: 'int' and 'str'",
    ],
    ["xx", "Exception in test line 1 column 0: name 'xx' is not defined"],
    [
        "(x, y) = (1, 2, 4)",
        "Exception in test line 1 column 16: too many values to unpack (expected 2)",
    ],
    [
        "(x, y, z) = (1, 2)",
        "Exception in test line 1 column 16: too few values to unpack (expected 3)",
    ],
    [
        "(x, y) = 1",
        "Exception in test line 1 column 9: cannot unpack non-iterable object",
    ],
    [
        "import math; math.sinXYZ",
        "Exception in test line 1 column 13: module 'math' has no attribute 'sinXYZ'",
    ],
    ["del xx", "Exception in test line 1 column 0: name 'xx' is not defined in del"],
    [
        "with None:\n    pass\n",
        "Exception in test line 1 column 0: test: not implemented ast ast_with",
    ],
    [
        "import cmath; exec('xyz = cmath.sqrt(complex(3, 4))', {})",
        "Exception in test line 1 column 54: Exception in exec() line 1 column 28: function 'sqrt' is not callable (got None)",
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
        "import asyncio",
        "Exception in test line 1 column 0: import of asyncio not allowed",
    ],
    [
        "from asyncio import xyz",
        "Exception in test line 1 column 0: import from asyncio not allowed",
    ],
    [
        """
def func():
    nonlocal x
    x = 1
func()
""",
        "Exception in func(), test line 4 column 4: can't find nonlocal 'x' for assignment",
    ],
    [
        """
def func():
    nonlocal x
    x += 1
func()
""",
        "Exception in func(), test line 4 column 4: nonlocal name 'x' is not defined",
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
        "Exception in test line 4 column 9: Exception in func(), eval() line 1 column 4: name 'y' is not defined",
    ],
]


async def run_one_test_exception(test_data, state_func, handler_func):
    """Run one interpreter test that generates an exception."""
    source, expect = test_data
    global_ctx = GlobalContext(
        "test",
        None,
        global_sym_table={},
        state_func=state_func,
        handler_func=handler_func,
    )
    ast = AstEval(
        "test", global_ctx=global_ctx, state_func=state_func, handler_func=handler_func
    )
    ast.parse(source)
    exc = ast.get_exception()
    if exc is not None:
        assert exc == expect
        return
    await ast.eval()
    exc = ast.get_exception()
    if exc is not None:
        assert exc == expect
        return
    assert False


def test_eval_exceptions(hass):
    """Test interpreter exceptions."""
    handler_func = handler.Handler(hass)
    state_func = state.State(hass, handler_func)
    state_func.register_functions()

    for test_data in evalTestsExceptions:
        asyncio.run(run_one_test_exception(test_data, state_func, handler_func))
