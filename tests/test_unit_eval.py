"""Unit tests for Python interpreter."""

import ast as ast_module
import asyncio
import contextlib
from dataclasses import dataclass
import gc
import inspect
import logging
import sys
from types import FunctionType, ModuleType
import warnings

import pytest
from pytest_homeassistant.common import MockConfigEntry

from custom_components.pyscript import DecoratorRegistry
from custom_components.pyscript.const import CONF_ALLOW_ALL_IMPORTS, CONFIG_ENTRY, DOMAIN
from custom_components.pyscript.eval import AstEval, EvalExceptionFormatter
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
    ["[0, 1, 2, 3, 4][4:1:-1]", [4, 3, 2]],
    ["a = 1; b = 6; c = 2; [0, 1, 2, 3, 4, 5, 6][a:b:c]", [1, 3, 5]],
    ["z = [0, 1, 2, 3, 4, 5, 6, 7, 8]; z[1:5:2] = [6, 8]; z", [0, 6, 2, 8, 4, 5, 6, 7, 8]],
    ["z = [0, 1, 2, 3, 4, 5, 6, 7, 8]; z[1:5] = [10, 11]; z", [0, 10, 11, 5, 6, 7, 8]],
    ["z = [0, 1, 2, 3, 4, 5, 6, 7, 8]; z[1::3] = [10, 11, 12]; z", [0, 10, 2, 3, 11, 5, 6, 12, 8]],
    ["z = [0, 1, 2, 3, 4, 5, 6, 7, 8]; z[4::] = [10, 11, 12, 13]; z", [0, 1, 2, 3, 10, 11, 12, 13]],
    ["z = [0, 1, 2, 3, 4, 5, 6, 7, 8]; z[4:] = [10, 11, 12, 13, 14]; z", [0, 1, 2, 3, 10, 11, 12, 13, 14]],
    ["z = [0, 1, 2, 3, 4, 5, 6, 7, 8]; z[:6:2] = [10, 11, 12]; z", [10, 1, 11, 3, 12, 5, 6, 7, 8]],
    ["z = [0, 1, 2, 3, 4, 5, 6, 7, 8]; z[:4:] = [10, 11, 12, 13]; z", [10, 11, 12, 13, 4, 5, 6, 7, 8]],
    ["z = [0, 1, 2, 3, 4, 5, 6, 7, 8]; z[::2] = [10, 11, 12, 13, 14]; z", [10, 1, 11, 3, 12, 5, 13, 7, 14]],
    ["x = [0, 1, 2, 3, 4]; x[1:4] = [9, 8]; x", [0, 9, 8, 4]],
    ["x = [0, 1, 2, 3, 4, 5]; x[::2] = [9, 8, 7]; x", [9, 1, 8, 3, 7, 5]],
    ["x = [0, 1, 2, 3, 4, 5]; del x[1:5:2]; x", [0, 2, 4, 5]],
    ["x = [0, 1, 2, 3, 4]; del x[::-2]; x", [1, 3]],
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
    [
        """
from enum import IntEnum

class TestIntMode(IntEnum):
    VAL1 = 1
    VAL2 = 2
    VAL3 = 3
[TestIntMode.VAL2 == 2, isinstance(TestIntMode.VAL3, IntEnum)]
""",
        [True, True],
    ],
    [
        """
from enum import StrEnum

class TestStrEnum(StrEnum):
    VAL1 = "val1"
    VAL2 = "val2"
    VAL3 = "val3"
[TestStrEnum.VAL2 == "val2", isinstance(TestStrEnum.VAL3, StrEnum)]
""",
        [True, True],
    ],
    [
        """
from enum import Enum, EnumMeta

class Color(Enum):
    RED = 1
    BLUE = 2
[type(Color) is EnumMeta, isinstance(Color.RED, Color), list(Color.__members__.keys())]
""",
        [True, True, ["RED", "BLUE"]],
    ],
    [
        """
from enum import Enum

class HState(Enum):
    HOME = "home"
    AWAY = "away"

    def name_and_value(self):
        return f"{self.name}:{self.value}"

[HState.HOME.name_and_value(), HState.AWAY.name_and_value()]
""",
        ["HOME:home", "AWAY:away"],
    ],
    [
        """
class Device:
    def greet(self, name):
        return f"hi {name} from {self.__class__.__name__}"

d = Device()
d.greet("Alice")
""",
        "hi Alice from Device",
    ],
    [
        """
def add(self, x, y=0):
    return x + y

class Calc:
    pass

Calc.add = add
Calc().add(2, 3)
""",
        5,
    ],
    [
        """
class Base:
    def tag(self):
        return "base"

class Child(Base):
    pass

Child().tag()
""",
        "base",
    ],
    [
        """
from dataclasses import dataclass

@dataclass()
class DT:
    name: str
    num: int = 32
obj1 = DT(name="abc")
obj2 = DT("xyz", 5)
[obj1.name, obj1.num, obj2.name, obj2.num]
""",
        ["abc", 32, "xyz", 5],
    ],
    [
        """
class Meta(type):
    def __new__(mcls, name, bases, ns, flag=False):
        ns["flag"] = flag
        return type.__new__(mcls, name, bases, ns)

class Foo(metaclass=Meta, flag=True):
    pass
[Foo.flag, isinstance(Foo, Meta)]
""",
        [True, True],
    ],
    [
        """
def deco(label):
    def wrap(cls):
        cls.labels.append(label)
        return cls
    return wrap

@deco("first")
@deco("second")
class Decorated:
    labels = []
Decorated.labels
""",
        ["second", "first"],
    ],
    [
        """
hits = []

def anno():
    hits.append("ok")
    return int

class Annotated:
    a: anno()
    b: int = 3
    c = "skip"
[hits, Annotated.__annotations__, Annotated.b, hasattr(Annotated, "c")]
""",
        [["ok"], {"a": int, "b": int}, 3, True],
    ],
    [
        """
class Foo:
    pass

class Bar:
    z: Foo | None = None
    w: list[Foo] = []
[Bar.__annotations__["z"] == (Foo | None), Bar.__annotations__["w"] == list[Foo], Bar.z]
""",
        [True, True, None],
    ],
    [
        """
from dataclasses import dataclass

class Foo:
    pass

@dataclass
class Bar:
    name: str
    z: Foo | None = None

foo = Foo()
obj1 = Bar("abc")
obj2 = Bar("xyz", foo)
[obj1.name, obj1.z, obj2.name, obj2.z is foo]
""",
        ["abc", None, "xyz", True],
    ],
    [
        """
class Node:
    nxt: Node = None
[Node.nxt, list(Node.__annotations__)]
""",
        [None, ["nxt"]],
    ],
    [
        """
try:
    class A:
        x = 1
        raise ValueError
except ValueError:
    pass
y = 2
class B:
    pass
['y' in globals(), 'B' in globals(), B.__name__]
""",
        [True, True, "B"],
    ],
    [
        """
g = 0
def f():
    global g
    try:
        class A:
            raise ValueError
    except ValueError:
        pass
    g = 5
f()
g
""",
        5,
    ],
    [
        """
class Foo:
    value = 1

def read():
    return Foo.value

def keep():
    global Foo
    Foo = Foo

keep()
Foo = type("Replacement", (), {"value": 2})
[Foo.value, read()]
""",
        [2, 2],
    ],
    [
        """
class Node:
    nxt: Node | None = None
    val: int = 0
    later: Later = None
[Node.nxt, Node.__annotations__ == {"nxt": Node | None, "val": int, "later": "Later"}]
""",
        [None, True],
    ],
    ["Foo = [type('Foo', (), {'x': 100})]; Foo[0].x = 10; Foo[0].x", 10],
    ["Foo = [type('Foo', (), {'x': [100, 101]})]; Foo[0].x[1] = 10; Foo[0].x", [100, 10]],
    ["Foo = [type('Foo', (), {'x': [0, [[100, 101]]]})]; Foo[0].x[1][0][1] = 10; Foo[0].x[1]", [[100, 10]]],
    [
        "Foo = [type('Foo', (), {'x': [0, [[100, 101, 102, 103]]]})]; Foo[0].x[1][0][1:2] = [11, 12]; Foo[0].x[1]",
        [[100, 11, 12, 102, 103]],
    ],
    [
        """
class A:
    pass

a = A()
a.x = 1
a.y = 2
del a.x
[hasattr(a, "x"), a.y, vars(a)]
""",
        [False, 2, {"y": 2}],
    ],
    [
        """
class Cache:
    def __init__(self):
        self.value = 5

    def clear(self):
        del self.value

    def has_value(self):
        return hasattr(self, "value")

cache = Cache()
before = cache.has_value()
cache.clear()
[before, cache.has_value()]
""",
        [True, False],
    ],
    [
        """
class A:
    pass

a = A()
results = []
try:
    del a.missing
    results.append("no error")
except AttributeError as exc:
    results.append("AttributeError")
a.items = [A()]
a.items[0].z = 3
del a.items[0].z
results.append(hasattr(a.items[0], "z"))
results
""",
        ["AttributeError", False],
    ],
    [
        """
import types

class A:
    x = 1
    y = 2

mod = types.ModuleType("mod")
mod.value = 10
del A.x
del mod.value
[hasattr(A, "x"), A.y, hasattr(mod, "value")]
""",
        [False, 2, False],
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
def f(x=None, /, **kw):
    return [x, kw]
[f(x=1), f(2, x=3), f(4)]
""",
        [[None, {"x": 1}], [2, {"x": 3}], [4, {}]],
    ],
    [
        """
def f(a, /, b, *, c, **kw):
    return [a, b, c, kw]
def g(a=1, b=2, /, c=3, **kw):
    return [a, b, c, kw]
[f(1, a=2, b=3, c=4), g(a=10, b=20, c=30)]
""",
        [[1, 3, 4, {"a": 2}], [1, 2, 30, {"a": 10, "b": 20}]],
    ],
    [
        """
class Box:
    def m(self, x=None, /, **kw):
        return [x, kw]
[Box().m(x=1), Box().m(2, x=3)]
""",
        [[None, {"x": 1}], [2, {"x": 3}]],
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
    bar3 = arg
    return bar
    bar += arg
    return 1000
[foo(), foo(5), bar, bar2, bar3]
""",
        [106, 111, 111, 1001, 5],
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
from functools import wraps

errors = []

def catch_all(func):
    @wraps(func)
    def wrapper(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except Exception as exc:
            errors.append(str(exc))
            return "caught"
    return wrapper

@catch_all
def fails(value):
    raise ValueError(f"bad value {value}")

[fails(1), fails(value=2), errors]
""",
        ["caught", "caught", ["bad value 1", "bad value 2"]],
    ],
    [
        """
from functools import wraps

calls = []

def trace(func):
    @wraps(func)
    def wrapper(*args, **kwargs):
        calls.append([args, kwargs])
        return func(*args, **kwargs) * 10
    return wrapper

@trace
def add(a, b=2, *, c=3):
    return a + b + c

[add(1), add(1, 5, c=7), add(a=4, b=0, c=0), calls]
""",
        [60, 130, 40, [[(1,), {}], [(1, 5), {"c": 7}], [(), {"a": 4, "b": 0, "c": 0}]]],
    ],
    [
        """
from functools import wraps

calls = []

def trace(func):
    @wraps(func)
    def wrapper(*args, **kwargs):
        calls.append(f"trace {func.__name__}")
        return func(*args, **kwargs)
    return wrapper

def double(func):
    def double_wrapper(*args, **kwargs):
        calls.append("double")
        return 2 * func(*args, **kwargs)
    return double_wrapper

@double
@trace
def inc1(x):
    return x + 1

@trace
@trace
def inc2(x):
    return x + 2

[inc1(1), inc2(1), calls]
""",
        [4, 3, ["double", "trace inc1", "trace inc2", "trace inc2"]],
    ],
    [
        """
registry = []

def register(func):
    registry.append(func)
    return func

@register
@register
def inc(x):
    return x + 1

[inc(1), registry[0](2), registry[0] is inc, registry[1] is inc]
""",
        [2, 3, True, True],
    ],
    [
        """
from functools import wraps

def trace(func):
    @wraps(func)
    def wrapper(*args, **kwargs):
        return func(*args, **kwargs)
    return wrapper

@trace
def inc(x):
    '''Add one to x.'''
    return x + 1

[inc.__name__, inc.__doc__, inc.__wrapped__(1), inc.__wrapped__.__name__, "__wrapped__" in vars(inc)]
""",
        ["inc", "Add one to x.", 2, "inc", True],
    ],
    [
        """
from functools import wraps

errors = []

def catch_all(func):
    @wraps(func)
    def wrapper(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except Exception as exc:
            errors.append(str(exc))
            return -1
    return wrapper

class Counter:
    def __init__(self):
        self.count = 0

    @catch_all
    def incr(self, step):
        if step < 0:
            raise ValueError(f"bad step {step}")
        self.count += step
        return self.count

counter = Counter()
[counter.incr(2), counter.incr(-1), counter.incr(3), errors, Counter.incr.__name__]
""",
        [2, -1, 5, ["bad step -1"], "incr"],
    ],
    [
        """
from functools import wraps

def trace(func):
    @wraps(func)
    def wrapper(*args, **kwargs):
        return func(*args, **kwargs)
    return wrapper

class Counter:
    def incr(self):
        '''Add one.'''
        return 1

    @trace
    def decr(self):
        '''Take one away.'''
        return -1

    def reset(self):
        return 0

counter = Counter()
[
    counter.incr.__doc__,
    counter.decr.__doc__,
    counter.decr.__name__,
    counter.reset.__doc__,
    Counter.incr.__doc__,
    Counter.decr.__doc__,
]
""",
        ["Add one.", "Take one away.", "decr", None, "Add one.", "Take one away."],
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
class Temp:
    def __init__(self):
        self.v = 4

    def name(self):
        return type(self).__name__

    def value(self):
        return self.v

method = Temp().value
[Temp().name(), Temp().value(), method()]
""",
        ["Temp", 4, 4],
    ],
    [
        """
x = []
class Temp:
    def method(self):
        return 1

    @pyscript_compile
    def __del__(self):
        x.append(-1)

Temp().method()
r1 = list(x)
method = Temp().method
r2 = list(x)
del method
[r1, r2, x]
""",
        [[-1], [-1], [-1, -1]],
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
    try:
        ast.parse(source)
        result = await ast.eval({"sym_local": 10}, merge_local=True)
        assert result == expect
    except Exception as exc:
        assert False, f"Parsing {source} failed: {exc!s}"


@pytest.mark.asyncio
async def test_eval(hass):
    """Test interpreter."""
    hass.data[DOMAIN] = {CONFIG_ENTRY: MockConfigEntry(domain=DOMAIN, data={CONF_ALLOW_ALL_IMPORTS: True})}
    Function.init(hass)
    State.init(hass)
    State.register_functions()
    TrigTime.init(hass)
    DecoratorRegistry.init(hass)

    for test_data in evalTests:
        await run_one_test(test_data)
    await Function.waiter_sync()
    await Function.waiter_stop()
    await Function.reaper_stop()


@dataclass
class ED:
    """Exception description."""

    exception: type[Exception]
    message: str
    lineno: int = 1
    col_offset: int = 0
    end_col_offset: int = 0


evalTestsExceptions = [
    [None, ED(TypeError, "compile() arg 1 must be a string, bytes or AST object")],
    ["1+", ED(SyntaxError, "invalid syntax (test, line 1)", col_offset=2, end_col_offset=3)],
    ["1+'x'", ED(TypeError, "unsupported operand type(s) for +: 'int' and 'str'", end_col_offset=5)],
    ["xx", ED(NameError, "name 'xx' is not defined", end_col_offset=2)],
    ["(x, y) = (1, 2, 4)", ED(ValueError, "too many values to unpack (expected 2)", end_col_offset=6)],
    ["(x, y) = iter([1, 2, 4])", ED(ValueError, "too many values to unpack (expected 2)", end_col_offset=6)],
    ["(x, y, z) = (1, 2)", ED(ValueError, "too few values to unpack (expected 3)", end_col_offset=9)],
    ["(x, y, z) = iter([1, 2])", ED(ValueError, "too few values to unpack (expected 3)", end_col_offset=9)],
    ["(x, y) = 1", ED(TypeError, "cannot unpack non-iterable object", end_col_offset=6)],
    ["x: int; x", ED(NameError, "name 'x' is not defined", col_offset=8, end_col_offset=9)],
    [
        "a, *y, w, z = range(2)",
        ED(ValueError, "too few values to unpack (expected at least 3)", end_col_offset=11),
    ],
    ["assert 1 == 0, 'this is an error'", ED(AssertionError, "this is an error", end_col_offset=33)],
    ["assert 1 == 0", ED(AssertionError, "", end_col_offset=13)],
    ["pyscript.var1.attr1 = 10", ED(NameError, "state pyscript.var1 doesn't exist", end_col_offset=19)],
    [
        "import math; math.sinXYZ",
        ED(AttributeError, "module 'math' has no attribute 'sinXYZ'", col_offset=13, end_col_offset=24),
    ],
    ["f'xxx{'", ED(SyntaxError, "f-string: expecting '}' (test, line 1)", col_offset=6, end_col_offset=6)],
    [
        "f'xxx{foo() i}'",
        ED(
            SyntaxError,
            "invalid syntax. Perhaps you forgot a comma? (test, line 1)",
            col_offset=6,
            end_col_offset=13,
        ),
    ],
    ["del xx", ED(NameError, "name 'xx' is not defined", end_col_offset=6)],
    [
        "pyscript.var1 = 1; del pyscript.var1; pyscript.var1",
        ED(NameError, "name 'pyscript.var1' is not defined", col_offset=38, end_col_offset=51),
    ],
    [
        "pyscript.var1 = 1; state.delete('pyscript.var1'); pyscript.var1",
        ED(NameError, "name 'pyscript.var1' is not defined", col_offset=50, end_col_offset=63),
    ],
    ["return", ED(SyntaxError, "return statement outside function")],
    ["break", ED(SyntaxError, "break statement outside loop")],
    [
        "i = 1; break; i = 1/0",
        ED(SyntaxError, "break statement outside loop"),
    ],
    ["continue", ED(SyntaxError, "continue statement outside loop")],
    ["raise", ED(RuntimeError, "No active exception to reraise", end_col_offset=5)],
    ["yield", ED(NotImplementedError, "test: not implemented ast ast_yield", end_col_offset=5)],
    ["task.executor(5)", ED(TypeError, "function 5 is not callable by task.executor", end_col_offset=16)],
    [
        "task.executor(task.sleep)",
        ED(
            TypeError,
            "function <bound method Function.async_sleep of "
            "<class 'custom_components.pyscript.function.Function'>> is not callable by task.executor",
            end_col_offset=25,
        ),
    ],
    ["task.name2id('notask')", ED(NameError, "task name 'notask' is unknown", end_col_offset=22)],
    [
        "state.get('pyscript.xyz1.abc')",
        ED(NameError, "name 'pyscript.xyz1' is not defined", end_col_offset=30),
    ],
    [
        "pyscript.xyz1 = 1; state.get('pyscript.xyz1.abc')",
        ED(AttributeError, "state 'pyscript.xyz1' has no attribute 'abc'", col_offset=19, end_col_offset=49),
    ],
    [
        "import cmath; exec('xyz = cmath.sqrt(complex(3, 4))', {})",
        ED(NameError, "name 'cmath.sqrt' is not defined", col_offset=6, end_col_offset=16),
    ],
    ["func1(1)", ED(NameError, "name 'func1' is not defined", end_col_offset=5)],
    [
        "def func(a):\n    pass\nfunc()",
        ED(TypeError, "func() missing 1 required positional arguments", lineno=3, end_col_offset=6),
    ],
    [
        "def func(a):\n    pass\nfunc(1, 2)",
        ED(TypeError, "func() called with too many positional arguments", lineno=3, end_col_offset=10),
    ],
    [
        "def func(a=1):\n    pass\nfunc(1, a=3)",
        ED(TypeError, "func() got multiple values for argument 'a'", lineno=3, end_col_offset=12),
    ],
    [
        "def func(*a, b):\n    pass\nfunc(1, 2)",
        ED(TypeError, "func() missing required keyword-only arguments", lineno=3, end_col_offset=10),
    ],
    [
        """
def func(b=1):
    pass

func(a=2, trigger_type=1)
""",
        ED(TypeError, "func() called with unexpected keyword arguments: a", lineno=5, end_col_offset=25),
    ],
    [
        """
def f(x, z, /, y = 5):
    return x + y
f(x=4, z=2)
""",
        ED(
            TypeError,
            "f() got some positional-only arguments passed as keyword arguments: 'x, z'",
            lineno=4,
            end_col_offset=11,
        ),
    ],
    [
        """
def f(x, /, y = 5):
    return x + y
f(x=4)
""",
        ED(
            TypeError,
            "f() got some positional-only arguments passed as keyword arguments: 'x'",
            lineno=4,
            end_col_offset=6,
        ),
    ],
    [
        """
def f(x, /, **kw):
    return [x, kw]
f(x=4)
""",
        ED(TypeError, "f() missing 1 required positional arguments", lineno=4, end_col_offset=6),
    ],
    [
        "from .xyz import abc",
        ED(ImportError, "attempted relative import with no known parent package", end_col_offset=20),
    ],
    [
        "from ...xyz import abc",
        ED(ImportError, "attempted relative import with no known parent package", end_col_offset=22),
    ],
    [
        "from . import abc",
        ED(ImportError, "attempted relative import with no known parent package", end_col_offset=17),
    ],
    ["import asyncio", ED(ModuleNotFoundError, "import of asyncio not allowed", end_col_offset=14)],
    ["import xyzabc123", ED(ModuleNotFoundError, "import of xyzabc123 not allowed", end_col_offset=16)],
    [
        "from asyncio import xyz",
        ED(ModuleNotFoundError, "import from asyncio not allowed", end_col_offset=23),
    ],
    [
        """
def func():
    nonlocal x
    x = 1
func()
""",
        ED(SyntaxError, "no binding for nonlocal 'x' found", lineno=2, end_col_offset=9),
    ],
    [
        """
def func():
    nonlocal x
    x += 1
func()
""",
        ED(SyntaxError, "no binding for nonlocal 'x' found", lineno=2, end_col_offset=10),
    ],
    [
        """
def func():
    global x
    return x
func()
""",
        ED(NameError, "global name 'x' is not defined", lineno=4, col_offset=11, end_col_offset=12),
    ],
    [
        """
def func():
    x = 1
    eval('1 + y')
func()
""",
        ED(NameError, "name 'y' is not defined", col_offset=4, end_col_offset=5),
    ],
    [
        """
try:
    d = {}
    x = d["bad_key"]
except KeyError:
    raise
""",
        ED(KeyError, "'bad_key'", lineno=4, col_offset=8, end_col_offset=20),
    ],
    [
        """
x = 0
def func():
    x += 1
func()
""",
        ED(
            UnboundLocalError,
            "local variable 'x' referenced before assignment",
            lineno=4,
            col_offset=4,
            end_col_offset=5,
        ),
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
        ED(NameError, "name 'x' is not defined", lineno=6, col_offset=8, end_col_offset=13),
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
        ED(NameError, "name 'x' is not defined", lineno=4, col_offset=12, end_col_offset=13),
    ],
    [
        """
@pyscript_compile(1)
def func():
    pass
""",
        ED(TypeError, "@pyscript_compile() takes 0 positional arguments", lineno=3, end_col_offset=8),
    ],
    [
        """
@pyscript_compile(invald_kw=True)
def func():
    pass
""",
        ED(TypeError, "@pyscript_compile() takes no keyword arguments", lineno=3, end_col_offset=8),
    ],
    [
        """
@pyscript_executor()
@pyscript_compile()
def func():
    pass
""",
        ED(
            SyntaxError,
            "can only specify single decorator of pyscript_compile, pyscript_executor",
            lineno=4,
            end_col_offset=8,
        ),
    ],
    [
        """
try:
    raise ValueError("Random error")
except KeyError:
    pass
""",
        ED(ValueError, "Random error", lineno=3, col_offset=4, end_col_offset=36),
    ],
    [
        """
try:
    raise ValueError("Random error")
except ValueError as e:
    pass

raise
""",
        ED(RuntimeError, "No active exception to reraise", lineno=7, end_col_offset=5),
    ],
    [
        """
try:
    raise ValueError("Random error")
except ValueError:
    raise
""",
        ED(ValueError, "Random error", lineno=3, col_offset=4, end_col_offset=36),
    ],
    [
        """
try:
    raise ValueError("Random error")
except ValueError as e:
    raise e
""",
        ED(ValueError, "Random error", lineno=3, col_offset=4, end_col_offset=36),
    ],
    [
        """
try:
    1 / 0
except ZeroDivisionError as e:
    raise ValueError("Random error") from e
""",
        ED(ValueError, "Random error", lineno=5, col_offset=4, end_col_offset=43),
    ],
    [
        """
try:
    1 / 0
except ZeroDivisionError:
    raise ValueError("Random error") from None
""",
        ED(ValueError, "Random error", lineno=5, col_offset=4, end_col_offset=46),
    ],
]


async def run_one_test_exception(test_data):
    """Run one interpreter test that generates an exception."""
    source, expect = test_data
    global_ctx = GlobalContext("test", global_sym_table={}, manager=GlobalContextMgr)
    ast = AstEval("test", global_ctx=global_ctx)
    Function.install_ast_funcs(ast)
    with pytest.raises(expect.exception) as exc:
        ast.parse(source)
        await ast.eval()
    assert str(exc.value) == expect.message
    formatted = EvalExceptionFormatter(exc.value)
    assert formatted.lineno == expect.lineno, (
        f"invalid line number {formatted.lineno} (expected {expect.lineno}) for {source}"
    )
    assert formatted.col_offset == expect.col_offset, (
        f"invalid column offset {formatted.col_offset} (expected {expect.col_offset}) for {source}"
    )
    assert formatted.end_col_offset == expect.end_col_offset, (
        f"invalid end column offset {formatted.end_col_offset} (expected {expect.end_col_offset}) for {source}"
    )


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


async def test_eval_exception_formatter(hass):
    """Test interpreter exception formatter."""
    hass.data[DOMAIN] = {CONFIG_ENTRY: MockConfigEntry(domain=DOMAIN, data={CONF_ALLOW_ALL_IMPORTS: False})}
    Function.init(hass)
    State.init(hass)
    State.register_functions()
    TrigTime.init(hass)

    module_name = "mod_exc"
    mod = ModuleType(module_name)
    module_ctx = GlobalContext(module_name, global_sym_table=mod.__dict__, manager=GlobalContextMgr)
    module_ctx.module = mod
    module_ctx.file_path = f"modules/{module_name}.py"
    module_ast = AstEval(module_name, global_ctx=module_ctx)

    module_ast.parse("""
def problem():
    return 0/0
    """)
    await module_ast.eval()
    GlobalContextMgr.set("modules.mod_exc", module_ctx)

    global_ctx = GlobalContext("test", global_sym_table={}, manager=GlobalContextMgr)
    ast = AstEval("test", global_ctx=global_ctx)
    Function.install_ast_funcs(ast)
    ast.parse("""
from mod_exc import problem

try:
    problem()
except ZeroDivisionError:
    unknown_func()
""")
    with pytest.raises(NameError, match="name 'unknown_func' is not defined") as excinfo:
        await ast.eval()
    exc = excinfo.value
    assert isinstance(exc.__context__, ZeroDivisionError)
    formatted = EvalExceptionFormatter(exc).format()

    expected_list = [
        """\
  File "test", line 5, in None
    problem()
    ~~~~~~~^^
""",
        """\
  File "modules/mod_exc.py", line 3, in problem
    return 0/0
           ~^~
""",
        "\nDuring handling of the above exception, another exception occurred:\n\n",
        """\
  File "test", line 7, in None
    unknown_func()
    ^^^^^^^^^^^^
""",
    ]
    for expected in expected_list:
        assert expected in formatted

    await Function.waiter_sync()
    await Function.waiter_stop()
    await Function.reaper_stop()


async def test_eval_exception_chain(hass):
    """Test chain of eval exceptions."""
    hass.data[DOMAIN] = {CONFIG_ENTRY: MockConfigEntry(domain=DOMAIN, data={CONF_ALLOW_ALL_IMPORTS: False})}
    Function.init(hass)
    global_ctx = GlobalContext("test", global_sym_table={}, manager=GlobalContextMgr)
    ast = AstEval("test", global_ctx=global_ctx)
    Function.install_ast_funcs(ast)
    ast.parse("""
def test_context():
    try:
        try:
            1 / 0
        except ZeroDivisionError:
            int("x")  # ValueError with __context__
    except ValueError as e:
        assert isinstance(e.__context__, ZeroDivisionError)
        assert e.__cause__ is None
        assert e.__suppress_context__ is False
    else:
        raise AssertionError("ValueError not raised")

def test_cause_raise_from():
    try:
        try:
            1 / 0
        except ZeroDivisionError as err:
            raise ValueError("bad") from err
    except ValueError as e:
        assert isinstance(e.__cause__, ZeroDivisionError)
    else:
        raise AssertionError("ValueError not raised")

def test_suppressed_from_none():
    try:
        try:
            1 / 0
        except ZeroDivisionError:
            raise ValueError("bad") from None
    except ValueError as e:
        assert e.__cause__ is None
        assert isinstance(e.__context__, ZeroDivisionError)  # stored, but suppressed in printing
        assert e.__suppress_context__ is True
    else:
        raise AssertionError("ValueError not raised")


def test_reraise():
    try:
        try:
            1 / 0
        except ZeroDivisionError:
            raise
    except ZeroDivisionError as e:
        assert e.__context__ is None
        assert e.__cause__ is None
    else:
        raise AssertionError("ZeroDivisionError not raised")

test_context()
test_cause_raise_from()
test_suppressed_from_none()
test_reraise()
    """)
    await ast.eval()

    await Function.waiter_sync()
    await Function.waiter_stop()
    await Function.reaper_stop()


WITH_SEMANTICS_SOURCE = """
import sys

class Ctx:
    def __init__(
        self, msgs, name, fail_enter=False, suppress=False, fail_exit=False, reraise=False, chain_exit=False
    ):
        self.msgs = msgs
        self.name = name
        self.fail_enter = fail_enter
        self.suppress = suppress
        self.fail_exit = fail_exit
        self.reraise = reraise
        self.chain_exit = chain_exit

    def __enter__(self):
        self.msgs.append(f"enter {self.name}")
        if self.fail_enter:
            raise ValueError(f"enter {self.name}")
        return self.name

    def __exit__(self, exc_type, exc, tb):
        active = sys.exc_info()[1]
        self.msgs.append(
            f"exit {self.name} {exc_type.__name__ if exc_type else None}"
            f" active {type(active).__name__ if active else None} same {active is exc}"
        )
        if self.fail_exit:
            raise KeyError(f"exit {self.name}")
        if self.chain_exit:
            try:
                raise KeyError(f"exit {self.name}")
            except KeyError:
                raise IndexError(f"exit {self.name}")
        if self.reraise:
            raise
        return self.suppress

class ACtx:
    def __init__(self, msgs, name, **kwargs):
        self.ctx = Ctx(msgs, name, **kwargs)

    async def __aenter__(self):
        return self.ctx.__enter__()

    async def __aexit__(self, exc_type, exc, tb):
        return self.ctx.__exit__(exc_type, exc, tb)

def make(msgs, name, **kwargs):
    msgs.append(f"make {name}")
    return Ctx(msgs, name, **kwargs)

def amake(msgs, name, **kwargs):
    msgs.append(f"make {name}")
    return ACtx(msgs, name, **kwargs)

def describe(exc):
    chain = []
    context = exc.__context__
    while context is not None:
        chain.append(type(context).__name__)
        context = context.__context__
    return f"raised {type(exc).__name__} {exc} context {' -> '.join(chain) if chain else None}"

results = []
cases = [
    {"b": {"fail_enter": True}},
    {"body": True, "b": {"suppress": True}},
    {"body": True, "a": {"suppress": True}},
    {"body": True, "b": {"fail_exit": True}},
    {"b": {"fail_exit": True}},
    {"body": True, "a": {"fail_exit": True}, "b": {"suppress": True}},
    {"body": True, "b": {"reraise": True}},
    {"body": True, "a": {"reraise": True}, "b": {"fail_exit": True}},
    {"body": True, "b": {"chain_exit": True}},
    {"body": True, "a": {"chain_exit": True}, "b": {"fail_exit": True}},
    {"a": {"chain_exit": True}},
    {},
]
for case in cases:
    msgs = []
    try:
        with make(msgs, "a", **case.get("a", {})) as a, make(msgs, "b", **case.get("b", {})) as b:
            msgs.append(f"body {a} {b}")
            if case.get("body"):
                raise ZeroDivisionError("body")
        msgs.append("no error")
    except Exception as exc:
        msgs.append(describe(exc))
    results.append(msgs)

    msgs = []
    try:
        async with amake(msgs, "a", **case.get("a", {})) as a, amake(msgs, "b", **case.get("b", {})) as b:
            msgs.append(f"body {a} {b}")
            if case.get("body"):
                raise ZeroDivisionError("body")
        msgs.append("no error")
    except Exception as exc:
        msgs.append(describe(exc))
    results.append(msgs)

msgs = []
try:
    async with failing_acm():
        msgs.append("body")
except Exception as exc:
    msgs.append(describe(exc))
results.append(msgs)

def with_return(msgs):
    with make(msgs, "a") as a, make(msgs, "b") as b:
        msgs.append("body")
        return f"returned {a} {b}"
    msgs.append("not reached")

msgs = []
msgs.append(with_return(msgs))
results.append(msgs)

msgs = []
for i in range(3):
    with make(msgs, f"loop{i}"), make(msgs, f"inner{i}"):
        if i == 0:
            continue
        if i == 1:
            break
    msgs.append(f"after {i}")
msgs.append("loop done")
results.append(msgs)
"""


@contextlib.asynccontextmanager
async def failing_acm():
    """Fail on enter, like a generator based manager whose connection is refused."""
    raise ConnectionRefusedError("refused")
    yield  # pylint: disable=unreachable


async def test_with_matches_python(hass):
    """Test that with and async with behave like Python, compared with CPython itself."""
    hass.data[DOMAIN] = {CONFIG_ENTRY: MockConfigEntry(domain=DOMAIN, data={CONF_ALLOW_ALL_IMPORTS: True})}
    Function.init(hass)
    State.init(hass)
    State.register_functions()
    TrigTime.init(hass)

    expected_ns = {"failing_acm": failing_acm}
    code = compile(WITH_SEMANTICS_SOURCE, "test", "exec", flags=ast_module.PyCF_ALLOW_TOP_LEVEL_AWAIT)
    # code compiled with top level await runs as a coroutine function, like the asyncio REPL
    await FunctionType(code, expected_ns)()
    expected = expected_ns["results"]

    global_ctx = GlobalContext(
        "test", global_sym_table={"failing_acm": failing_acm}, manager=GlobalContextMgr
    )
    ast = AstEval("test", global_ctx=global_ctx)
    Function.install_ast_funcs(ast)
    ast.parse(WITH_SEMANTICS_SOURCE + "results\n")
    assert await ast.eval() == expected

    await Function.waiter_sync()
    await Function.waiter_stop()
    await Function.reaper_stop()


WITH_CANCEL_SOURCE = """
class Suppress:
    def __enter__(self):
        msgs.append("enter")

    def __exit__(self, exc_type, exc, tb):
        msgs.append(f"exit {exc_type.__name__ if exc_type else None}")
        return True

class ASuppress:
    async def __aenter__(self):
        msgs.append("aenter")

    async def __aexit__(self, exc_type, exc, tb):
        msgs.append(f"aexit {exc_type.__name__ if exc_type else None}")
        return True

with Suppress():
    async with ASuppress():
        msgs.append("body")
        started.set()
        await forever.wait()
    msgs.append("after async with")
msgs.append("after with")
"""


async def test_with_exit_does_not_swallow_cancel(hass):
    """Test that an exit returning true still lets a task be cancelled."""
    hass.data[DOMAIN] = {CONFIG_ENTRY: MockConfigEntry(domain=DOMAIN, data={CONF_ALLOW_ALL_IMPORTS: True})}
    Function.init(hass)
    State.init(hass)
    State.register_functions()
    TrigTime.init(hass)

    msgs = []
    started = asyncio.Event()
    forever = asyncio.Event()
    global_ctx = GlobalContext(
        "test",
        global_sym_table={"msgs": msgs, "started": started, "forever": forever},
        manager=GlobalContextMgr,
    )
    ast = AstEval("test", global_ctx=global_ctx)
    Function.install_ast_funcs(ast)
    ast.parse(WITH_CANCEL_SOURCE)
    task = asyncio.create_task(ast.eval())
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert msgs == ["enter", "aenter", "body", "aexit CancelledError", "exit CancelledError"]

    await Function.waiter_sync()
    await Function.waiter_stop()
    await Function.reaper_stop()


GLOBAL_MOD_NAME = "mod_global"

GLOBAL_MOD_SOURCE = """
class Foo:
    x = 1

def modread():
    return Foo

def drop():
    global Foo
    del Foo
"""

GLOBAL_BASE_SOURCE = """
class Foo:
    x = 1

class Other:
    x = 9

def read():
    return Foo

def try_call(func):
    try:
        func()
        return "no error"
    except NameError:
        return "NameError"
"""

#
# each entry is a list of scripts and the value of "result" in the last one, which
# is what CPython gives; the scripts share one fresh copy of the GLOBAL_MOD_SOURCE module
#
globalNameTests = [
    [
        [
            GLOBAL_BASE_SOURCE
            + """
def f():
    global Foo
    return [Foo | None, Foo is type(Foo())]
union, same = f()
result = [union == (Foo | None), same]
"""
        ],
        [True, True],
    ],
    [
        [
            GLOBAL_BASE_SOURCE
            + """
def f():
    global Foo
    Foo.x = 3
    return [Foo.x, read().x]
result = [f(), Foo.x]
"""
        ],
        [[3, 3], 3],
    ],
    [
        [
            GLOBAL_BASE_SOURCE
            + """
def rebind():
    global Foo
    Foo = Other
rebind()
result = [read() is Other, Foo is Other]
"""
        ],
        [True, True],
    ],
    [
        [
            GLOBAL_BASE_SOURCE
            + """
def drop():
    global Foo
    del Foo
def read_global_attr():
    global Foo
    return Foo.x
drop()
result = [try_call(read), try_call(read_global_attr), try_call(drop)]
"""
        ],
        ["NameError", "NameError", "NameError"],
    ],
    [
        [
            GLOBAL_BASE_SOURCE
            + """
def drop():
    global Foo
    del Foo
drop()
class Foo:
    x = 5
result = [read().x, read() is Foo]
"""
        ],
        [5, True],
    ],
    [
        [
            GLOBAL_BASE_SOURCE
            + """
def drop():
    global Foo
    del Foo
def make():
    global Foo
    class Foo:
        x = 7
drop()
make()
result = [read().x, read() is Foo, Foo.x]
"""
        ],
        [7, True, 7],
    ],
    [
        [
            """
def make():
    global Node
    class Node:
        nxt: Node = None
make()
result = [Node.nxt, list(Node.__annotations__)]
"""
        ],
        [None, ["nxt"]],
    ],
    [
        [
            GLOBAL_BASE_SOURCE
            + """
class Foo:
    x = Foo.x + 4
result = [read().x, read() is Foo]
"""
        ],
        [5, True],
    ],
    [
        [
            GLOBAL_BASE_SOURCE
            + """
del Foo
class Foo:
    x = 5
result = [read().x, read() is Foo]
"""
        ],
        [5, True],
    ],
    [
        [
            GLOBAL_BASE_SOURCE
            + """
def outer():
    def inner():
        global Foo
        Foo = Other
    inner()
    return Foo
def outer_union():
    def inner():
        global Foo
        return Foo | None
    return inner()
result = [outer_union() == (Foo | None), outer() is Other, read() is Other]
"""
        ],
        [True, True, True],
    ],
    [
        [
            GLOBAL_BASE_SOURCE
            + """
def outer():
    def inner():
        return Foo
    return inner
inner = outer()
def rebind():
    global Foo
    Foo = Other
rebind()
result = [inner() is Other, read() is Other]
"""
        ],
        [True, True],
    ],
    [
        [
            GLOBAL_BASE_SOURCE
            + """
from mod_global import Foo, modread
def rebind():
    global Foo
    Foo = Other
rebind()
result = [Foo is Other, modread().x, modread() is Other]
"""
        ],
        [True, 1, False],
    ],
    [
        [
            GLOBAL_BASE_SOURCE
            + """
from mod_global import Foo, modread
same = [Foo is modread(), read() is modread()]
def drop():
    global Foo
    del Foo
drop()
result = [same, try_call(read), modread().x]
"""
        ],
        [[True, True], "NameError", 1],
    ],
    [
        [
            GLOBAL_BASE_SOURCE
            + """
from mod_global import Foo, modread
Foo = Other
result = [Foo is Other, modread().x, modread() is Other]
"""
        ],
        [True, 1, False],
    ],
    [
        [
            GLOBAL_BASE_SOURCE
            + """
from mod_global import Foo, modread
del Foo
result = [try_call(read), modread().x]
"""
        ],
        ["NameError", 1],
    ],
    [
        [
            GLOBAL_BASE_SOURCE
            + """
from mod_global import *
def rebind():
    global Foo
    Foo = Other
rebind()
result = [Foo is Other, modread().x, modread() is Other]
"""
        ],
        [True, 1, False],
    ],
    [
        [
            GLOBAL_BASE_SOURCE
            + """
from mod_global import *
del Foo
result = [try_call(read), modread().x]
"""
        ],
        ["NameError", 1],
    ],
    [
        [
            GLOBAL_BASE_SOURCE
            + """
from mod_global import Foo
def rebind():
    global Foo
    Foo = Other
rebind()
""",
            GLOBAL_BASE_SOURCE
            + """
from mod_global import *
def drop():
    global Foo
    del Foo
drop()
""",
            GLOBAL_BASE_SOURCE
            + """
from mod_global import Foo
Foo = Other
""",
            """
from mod_global import Foo, modread
result = [Foo.x, modread().x, Foo is modread()]
""",
        ],
        [1, 1, True],
    ],
    [
        [
            """
import mod_global
Foo = mod_global.Foo
class Other:
    x = 9
def rebind():
    global Foo
    Foo = Other
rebind()
result = [Foo.x, Foo is Other, mod_global.modread().x]
"""
        ],
        [9, True, 1],
    ],
    [
        [
            """
import mod_global
Foo = mod_global.Foo
same = Foo is mod_global.modread()
def read_foo():
    return Foo.x
def drop_alias():
    global Foo
    del Foo
drop_alias()
try:
    read_foo()
    gone = False
except NameError:
    gone = True
result = [same, gone, mod_global.modread().x]
"""
        ],
        [True, True, 1],
    ],
    [
        [
            """
import mod_global
Foo = mod_global.Foo
class Foo:
    x = 9
result = [Foo.x, mod_global.modread().x]
"""
        ],
        [9, 1],
    ],
    [
        [
            """
import mod_global
Foo = mod_global.Foo
class Other:
    x = 9
def rebind():
    global Foo
    Foo = Other
rebind()
""",
            """
import mod_global
Foo = mod_global.Foo
same = Foo is mod_global.modread()
def read_foo():
    return Foo.x
def drop_alias():
    global Foo
    del Foo
drop_alias()
try:
    read_foo()
    gone = False
except NameError:
    gone = True
""",
            """
import mod_global
Foo = mod_global.Foo
class Foo:
    x = 9
""",
            """
from mod_global import Foo, modread
result = [Foo.x, modread().x, Foo is modread()]
""",
        ],
        [1, 1, True],
    ],
    [
        [
            """
class Original:
    x = 1
Foo = globals()["Original"]
class Other:
    x = 9
def rebind():
    global Foo
    Foo = Other
rebind()
result = [Original.x, Foo.x, Foo is Other]
"""
        ],
        [1, 9, True],
    ],
    [
        [
            """
def outer():
    from mod_global import Foo
    def inner():
        return Foo.x
    return inner()
def outer_as():
    from mod_global import Foo as Local
    def inner():
        return Local.x
    return inner
def outer_import():
    import mod_global as mod
    def inner():
        return mod.modread().x
    return inner()
result = [outer(), outer_as()(), outer_import()]
"""
        ],
        [1, 1, 1],
    ],
    [
        [
            """
def outer():
    from mod_global import Foo, modread
    def read():
        return Foo
    def change():
        nonlocal Foo
        Foo = 20
    change()
    return [Foo, read(), modread().x]
result = outer()
"""
        ],
        [20, 20, 1],
    ],
    [
        [
            """
from mod_global import modread
def load():
    global Foo, mod_global
    from mod_global import Foo
    import mod_global
load()
result = [Foo.x, Foo is modread(), mod_global.modread() is Foo]
"""
        ],
        [1, True, True],
    ],
    [
        [
            """
import mod_global
mod_global.drop()
try:
    from mod_global import Foo
    msg = None
except ImportError as exc:
    msg = str(exc)
from mod_global import *
def read_foo():
    return Foo.x
try:
    read_foo()
    gone = False
except NameError:
    gone = True
result = [msg, gone]
"""
        ],
        ["cannot import name 'Foo' from 'mod_global' (unknown location)", True],
    ],
    [
        [
            """
class Foo:
    x = 1
def make():
    global Foo
    class C:
        from math import pi as Foo
    class D:
        import math as Foo
    class E:
        Foo = 3
    return [C, D, E]
C, D, E = make()
result = [isinstance(C.Foo, float), D.Foo.__name__, E.Foo, Foo.x]
"""
        ],
        [True, "math", 3, 1],
    ],
    [
        [
            """
class Foo:
    x = 1
def make():
    global Foo
    class C:
        Foo = 3
        y = Foo
        del Foo
    class D:
        def Foo(self):
            return 2
    class E:
        class Foo:
            x = 4
    return [C, D, E]
C, D, E = make()
result = [C.y, hasattr(C, "Foo"), D().Foo(), E.Foo.x, Foo.x]
"""
        ],
        [3, False, 2, 4, 1],
    ],
    [
        [
            """
class Foo:
    x = 1
class C:
    class Foo:
        x = 2
    from math import pi as Foo
    def read(self):
        return Foo.x
class D:
    class Foo:
        x = 2
    Foo = 5
    def read(self):
        return Foo.x
result = [C().read(), isinstance(C.Foo, float), D().read(), D.Foo]
"""
        ],
        [1, True, 1, 5],
    ],
    [
        [
            """
import mod_global
globals()["Foo"] = mod_global.Foo
class Other:
    x = 9
def rebind():
    global Foo
    Foo = Other
rebind()
result = [Foo.x, mod_global.modread().x]
"""
        ],
        [9, 1],
    ],
    [
        [
            """
import mod_global
globals().update({"Foo": mod_global.Foo})
class Foo:
    x = 9
result = [Foo.x, mod_global.modread().x]
"""
        ],
        [9, 1],
    ],
    [
        [
            """
import mod_global
Foo = mod_global.modread()
result = [
    (mod_global.Foo | None) == (Foo | None),
    mod_global.Foo is Foo,
    isinstance(Foo(), mod_global.Foo),
    type(mod_global.Foo()) is mod_global.Foo,
]
"""
        ],
        [True, True, True, True],
    ],
    [
        [
            """
import mod_global
from mod_global import Foo as Snap
class Other:
    x = 9
mod_global.Foo = Other
result = [mod_global.Foo is Other, mod_global.modread() is Other, Snap.x]
"""
        ],
        [True, True, 1],
    ],
    [
        [
            """
import mod_global
del mod_global.Foo
try:
    mod_global.modread()
    called = "no error"
except NameError:
    called = "NameError"
try:
    mod_global.Foo
    read = "no error"
except AttributeError:
    read = "AttributeError"
try:
    del mod_global.Foo
    again = "no error"
except AttributeError:
    again = "AttributeError"
result = [called, read, again, hasattr(mod_global, "Foo")]
"""
        ],
        ["NameError", "AttributeError", "AttributeError", False],
    ],
    [
        [
            """
import math
def f():
    def inner():
        return math.sqrt(4)
    try:
        before = math.sqrt(4)
    except UnboundLocalError:
        before = "unbound"
    import math
    return [before, inner()]
def g():
    def inner():
        return x
    x = 1
    del x
    try:
        x
        return "no error"
    except UnboundLocalError:
        return "unbound"
result = [f(), g()]
"""
        ],
        [["unbound", 2.0], "unbound"],
    ],
    [
        [
            GLOBAL_BASE_SOURCE
            + """
x = 1
def make():
    class C:
        global x, Foo
        x = 2
        from mod_global import Foo
    return C
C = make()
import mod_global
result = [x, hasattr(C, "x"), hasattr(C, "Foo"), read() is mod_global.Foo]
"""
        ],
        [2, False, False, True],
    ],
    [
        [
            GLOBAL_BASE_SOURCE
            + """
x = 1
class C:
    global x, Foo
    x = 2
    from math import pi as Foo
    y = x + 1
result = [x, hasattr(C, "x"), C.y, hasattr(C, "Foo"), isinstance(read(), float)]
"""
        ],
        [2, False, 3, False, True],
    ],
    [
        [
            """
x = 1
def f():
    class C:
        global x
        x = 2
    x = 5
    return x
def g():
    global x
    class C:
        x = 3
    return C.x
result = [f(), x, g(), x]
"""
        ],
        [5, 2, 3, 2],
    ],
    [
        [
            """
def pyscript_compile(func):
    return func
@pyscript_compile
def make_meta():
    class Namespace:
        def __init__(self):
            self.values = {}
        def __getitem__(self, name):
            return self.values[name]
        def __setitem__(self, name, value):
            self.values[name] = value
        def __contains__(self, name):
            return name in self.values
    class Meta(type):
        @classmethod
        def __prepare__(mcs, name, bases):
            return Namespace()
        def __new__(mcs, name, bases, namespace):
            return type.__new__(mcs, name, bases, dict(namespace.values))
    return Meta
class C(metaclass=make_meta()):
    class D:
        x = 3
    y = D.x
result = [C.D.x, C.y]
"""
        ],
        [3, 3],
    ],
    [
        [
            """
import mod_global
class Other:
    x = 9
result = [
    mod_global.Foo is mod_global.modread(),
    (mod_global.Foo | None) == (mod_global.modread() | None),
    getattr(mod_global, "Foo") is mod_global.modread(),
    vars(mod_global)["Foo"] is mod_global.modread(),
]
mod_global.Foo = Other
result.append(mod_global.modread() is Other)
"""
        ],
        [True, True, True, True, True],
    ],
    [
        [
            """
class Local:
    x = 2
import mod_global
get_local = lambda: Local
get_mod = lambda: mod_global.Foo
result = [
    globals()["Local"] is Local,
    (get_local() | None) == (Local | None),
    get_mod() is mod_global.modread(),
]
"""
        ],
        [True, True, True],
    ],
    [
        [
            """
def pyscript_compile(func):
    return func
from mod_global import Foo
class Local:
    pass
@pyscript_compile
def native():
    return [Local | None, Foo | None, globals()["Local"]]
union_local, union_foo, local = native()
result = [union_local == (Local | None), union_foo == (Foo | None), local is Local]
"""
        ],
        [True, True, True],
    ],
    [
        [
            """
try:
    class A:
        y = 1
        raise ValueError
except ValueError:
    pass
def make():
    try:
        class B:
            raise ValueError
    except ValueError:
        pass
    return "B" in locals()
result = ["A" in globals(), make()]
"""
        ],
        [False, False],
    ],
    [
        [
            """
from dataclasses import dataclass, field
@dataclass
class Node:
    val: int = 0
    nxt: Node | None = None
    kids: list[Node] = field(default_factory=list)
def make_local():
    class Item:
        def copy(self):
            return Item()
    def get():
        return Item
    first = get()
    class Item:
        pass
    return [type(first().copy()) is first, get() is Item]
try:
    class Bad:
        def m(self, x=Bad):
            return x
    default = "ok"
except NameError:
    default = "NameError"
n = Node(1, Node(2))
result = [n.nxt.val, len(n.kids), make_local(), default]
"""
        ],
        [2, 0, [False, True], "NameError"],
    ],
    [
        [
            """
import typing
from dataclasses import dataclass, InitVar, fields
@dataclass
class Node:
    val: int = 0
    nxt: Node | None = None
    parent: InitVar[Node] = None
result = [
    [f.name for f in fields(Node)],
    typing.get_type_hints(Node)["nxt"] == (Node | None),
    type(Node.__annotations__["parent"]) is InitVar,
]
"""
        ],
        [["val", "nxt"], True, True],
    ],
    [
        [
            """
class A:
    Alias = int
    x: A | Alias = None
    y: A | Later = None
    Later = str
class B:
    z: B | B = None
    B = float
result = [
    A.__annotations__["x"] == (A | int),
    A.__annotations__["y"] == (A | str),
    B.__annotations__["z"] == float,
]
"""
        ],
        [True, True, True],
    ],
]


def run_global_name_test_python(sources):
    """Run the scripts in CPython and return the last one's result."""
    mod = ModuleType(GLOBAL_MOD_NAME)
    FunctionType(compile(GLOBAL_MOD_SOURCE, GLOBAL_MOD_NAME, "exec"), mod.__dict__)()
    sys.modules[GLOBAL_MOD_NAME] = mod
    try:
        for source in sources:
            namespace = {}
            FunctionType(compile(source, "test", "exec"), namespace)()
        return namespace["result"]
    finally:
        del sys.modules[GLOBAL_MOD_NAME]


async def run_global_name_test(sources):
    """Run the scripts in pyscript and return the last one's result."""
    mod = ModuleType(GLOBAL_MOD_NAME)
    module_ctx = GlobalContext(GLOBAL_MOD_NAME, global_sym_table=mod.__dict__, manager=GlobalContextMgr)
    module_ctx.module = mod
    module_ctx.file_path = f"modules/{GLOBAL_MOD_NAME}.py"
    module_ast = AstEval(GLOBAL_MOD_NAME, global_ctx=module_ctx)
    Function.install_ast_funcs(module_ast)
    module_ast.parse(GLOBAL_MOD_SOURCE)
    await module_ast.eval()
    GlobalContextMgr.set(f"modules.{GLOBAL_MOD_NAME}", module_ctx)
    try:
        for source in sources:
            global_ctx = GlobalContext("test", global_sym_table={}, manager=GlobalContextMgr)
            ast = AstEval("test", global_ctx=global_ctx)
            Function.install_ast_funcs(ast)
            ast.parse(source)
            await ast.eval()
        return global_ctx.global_sym_table["result"]
    finally:
        GlobalContextMgr.delete(f"modules.{GLOBAL_MOD_NAME}")


async def test_global_names_match_python(hass):
    """Test module-level names used through global and from-imports, compared with CPython."""
    hass.data[DOMAIN] = {CONFIG_ENTRY: MockConfigEntry(domain=DOMAIN, data={CONF_ALLOW_ALL_IMPORTS: True})}
    Function.init(hass)
    State.init(hass)
    State.register_functions()
    TrigTime.init(hass)
    DecoratorRegistry.init(hass)

    for sources, expect in globalNameTests:
        assert run_global_name_test_python(sources) == expect, sources[-1]
        assert await run_global_name_test(sources) == expect, sources[-1]
    await Function.waiter_sync()
    await Function.waiter_stop()
    await Function.reaper_stop()


async def test_class_body_exception_keeps_old_class(hass):
    """Test that a class redefinition whose body raises keeps the old class, as in CPython."""
    hass.data[DOMAIN] = {CONFIG_ENTRY: MockConfigEntry(domain=DOMAIN, data={CONF_ALLOW_ALL_IMPORTS: True})}
    Function.init(hass)
    State.init(hass)
    State.register_functions()
    TrigTime.init(hass)

    source = (
        GLOBAL_BASE_SOURCE
        + """
class Foo:
    x = 5
    raise ValueError("bad class body")
"""
    )
    check = "result = [Foo.x, read().x, read() is Foo]"
    expect = [1, 1, True]

    namespace = {}
    with pytest.raises(ValueError, match="bad class body"):
        FunctionType(compile(source, "test", "exec"), namespace)()
    FunctionType(compile(check, "test", "exec"), namespace)()
    assert namespace["result"] == expect

    global_ctx = GlobalContext("test", global_sym_table={}, manager=GlobalContextMgr)
    ast = AstEval("test", global_ctx=global_ctx)
    Function.install_ast_funcs(ast)
    ast.parse(source)
    with pytest.raises(ValueError, match="bad class body"):
        await ast.eval()
    ast = AstEval("test", global_ctx=global_ctx)
    Function.install_ast_funcs(ast)
    ast.parse(check)
    await ast.eval()
    assert global_ctx.global_sym_table["result"] == expect

    await Function.waiter_sync()
    await Function.waiter_stop()
    await Function.reaper_stop()


CLASS_BODY_RAISES_SOURCE = """
results = {}

try:
    class Foo:
        a = 1
        raise ValueError("boom")
except ValueError:
    pass
x = 5
results["module"] = [x, "x" in globals()]

def func():
    try:
        class Bar:
            b = 2
            raise ValueError("boom")
    except ValueError:
        pass
    y = 7
    z = [y, "y" in locals()]
    return z

results["func"] = func()
results["func_global"] = "y" in globals()

class Outer:
    c = 3
    try:
        class Inner:
            d = 4
            raise ValueError("boom")
    except ValueError:
        pass
    e = 5

results["nested"] = [Outer.c, Outer.e, hasattr(Outer, "d")]
"""


async def test_class_body_raises_matches_python(hass):
    """Test that a class body that raises leaves the scope like Python, compared with CPython itself."""
    hass.data[DOMAIN] = {CONFIG_ENTRY: MockConfigEntry(domain=DOMAIN, data={CONF_ALLOW_ALL_IMPORTS: True})}
    Function.init(hass)
    State.init(hass)
    State.register_functions()
    TrigTime.init(hass)

    expected_ns = {}
    exec(compile(CLASS_BODY_RAISES_SOURCE, "test", "exec"), expected_ns)  # pylint: disable=exec-used
    expected = expected_ns["results"]

    global_ctx = GlobalContext("test", global_sym_table={}, manager=GlobalContextMgr)
    ast = AstEval("test", global_ctx=global_ctx)
    Function.install_ast_funcs(ast)
    ast.parse(CLASS_BODY_RAISES_SOURCE + "results\n")
    assert await ast.eval() == expected
    assert "x" in global_ctx.global_sym_table
    assert not ast.sym_table_stack

    await Function.waiter_sync()
    await Function.waiter_stop()
    await Function.reaper_stop()


async def test_class_body_error_reaches_caller(hass):
    """Test that an uncaught class body error reaches the caller and later runs are unaffected."""
    hass.data[DOMAIN] = {CONFIG_ENTRY: MockConfigEntry(domain=DOMAIN, data={CONF_ALLOW_ALL_IMPORTS: True})}
    Function.init(hass)
    State.init(hass)
    State.register_functions()
    TrigTime.init(hass)

    for source in [
        'class Foo:\n    raise ValueError("boom")\n',
        "class Foo:\n    return 1\n",
        "class Foo:\n    break\n",
    ]:
        with pytest.raises((ValueError, SyntaxError)):
            exec(compile(source, "test", "exec"), {})  # pylint: disable=exec-used

    global_ctx = GlobalContext("test", global_sym_table={}, manager=GlobalContextMgr)
    ast = AstEval("test", global_ctx=global_ctx)
    Function.install_ast_funcs(ast)
    for source, exc_type in [
        ('class Foo:\n    raise ValueError("boom")\n', ValueError),
        ("class Foo:\n    for i in range(2):\n        pass\n    raise KeyError(1)\n", KeyError),
    ]:
        ast.parse(source)
        with pytest.raises(exc_type):
            await ast.eval()
        assert ast.sym_table is ast.global_sym_table
        assert not ast.sym_table_stack

        ast.parse("x = 5\nx\n")
        assert await ast.eval() == 5
        assert global_ctx.global_sym_table["x"] == 5

    await Function.waiter_sync()
    await Function.waiter_stop()
    await Function.reaper_stop()


NESTED_CLASS_CASES = {
    "union_module_level": """
class Outer:
    class Inner:
        pass
res = (Outer.Inner | None) == (Outer.Inner | None)
""",
    "union_other_class_annotation": """
class Outer:
    class Inner:
        pass
class Bar:
    y: Outer.Inner | None = None
res = [Bar.y, Bar.__annotations__['y'] == (Outer.Inner | None)]
""",
    "identity": """
class Outer:
    class Inner:
        pass
res = [type(Outer.Inner()) is Outer.Inner, isinstance(Outer.Inner(), Outer.Inner)]
""",
    "dataclass": """
from dataclasses import dataclass
@dataclass
class Outer:
    @dataclass
    class Inner:
        a: int = 1
    x: Inner | None = None
res = [Outer().x, Outer(Outer.Inner()).x.a, type(Outer.Inner()) is Outer.Inner]
""",
    "in_function": """
def make():
    class Outer:
        class Inner:
            pass
    return Outer
O = make()
res = [type(O.__dict__['Inner']) is type, (O.Inner | None) == (O.Inner | None)]
""",
    "bare_name_in_method": """
class Outer:
    class Inner:
        v = 4
    def make(self):
        return Inner.v
try:
    res = [Outer().make()]
except NameError:
    res = ['NameError', Outer.Inner.v]
""",
    "two_levels": """
class Outer:
    class Mid:
        class Inner:
            v = 7
res = [
    Outer.Mid.Inner.v,
    type(Outer.Mid.Inner()) is Outer.Mid.Inner,
    (Outer.Mid.Inner | None) == (Outer.Mid.Inner | None),
]
""",
}


@pytest.mark.parametrize("name", list(NESTED_CLASS_CASES))
async def test_nested_class_attr_matches_python(hass, name):
    """Test that a class defined in a class body is the class itself, compared with CPython."""
    hass.data[DOMAIN] = {CONFIG_ENTRY: MockConfigEntry(domain=DOMAIN, data={CONF_ALLOW_ALL_IMPORTS: True})}
    Function.init(hass)
    State.init(hass)
    State.register_functions()
    TrigTime.init(hass)
    DecoratorRegistry.init(hass)

    source = NESTED_CLASS_CASES[name]
    expected_ns: dict = {}
    exec(source, expected_ns)  # pylint: disable=exec-used
    expected = expected_ns["res"]

    global_ctx = GlobalContext("test", global_sym_table={}, manager=GlobalContextMgr)
    ast = AstEval("test", global_ctx=global_ctx)
    Function.install_ast_funcs(ast)
    ast.parse(source)
    try:
        await ast.eval()
        assert global_ctx.global_sym_table["res"] == expected
    finally:
        await Function.waiter_sync()
        await Function.waiter_stop()
        await Function.reaper_stop()


class NoKeysNamespace:
    """Class namespace that supports item access but has no keys() or items()."""

    def __init__(self):
        """Initialize the namespace."""
        self.d = {}

    def __setitem__(self, key, value):
        """Set an item."""
        self.d[key] = value

    def __getitem__(self, key):
        """Get an item."""
        return self.d[key]

    def __contains__(self, key):
        """Check for an item."""
        return key in self.d

    def __delitem__(self, key):
        """Delete an item."""
        del self.d[key]


class NoKeysMeta(type):
    """Metaclass whose __prepare__ returns a namespace without keys()."""

    @classmethod
    def __prepare__(mcs, name, bases, **kwargs):
        """Return the custom namespace."""
        return NoKeysNamespace()

    def __new__(mcs, name, bases, ns, **kwargs):
        """Build the class from the custom namespace."""
        return super().__new__(mcs, name, bases, dict(ns.d))


NESTED_PREPARE_SOURCE = """
class Outer(metaclass=NoKeysMeta):
    class Inner:
        pass
    v = 3

res = [Outer.v, Outer.Inner is not None]
"""


async def test_nested_class_prepare_without_keys(hass):
    """Test that a class namespace from __prepare__ that can't be iterated still builds the class."""
    hass.data[DOMAIN] = {CONFIG_ENTRY: MockConfigEntry(domain=DOMAIN, data={CONF_ALLOW_ALL_IMPORTS: True})}
    Function.init(hass)
    State.init(hass)
    State.register_functions()
    TrigTime.init(hass)
    DecoratorRegistry.init(hass)

    expected_ns: dict = {"NoKeysMeta": NoKeysMeta}
    exec(NESTED_PREPARE_SOURCE, expected_ns)  # pylint: disable=exec-used
    global_ctx = GlobalContext("test", global_sym_table={"NoKeysMeta": NoKeysMeta}, manager=GlobalContextMgr)
    ast = AstEval("test", global_ctx=global_ctx)
    Function.install_ast_funcs(ast)
    ast.parse(NESTED_PREPARE_SOURCE)
    await ast.eval()
    assert global_ctx.global_sym_table["res"] == expected_ns["res"] == [3, True]

    await Function.waiter_sync()
    await Function.waiter_stop()
    await Function.reaper_stop()


class RejectSecondRewrite(dict):
    """Class namespace that refuses to rebind "Second"."""

    def __setitem__(self, key, value):
        """Set an item, but only allow "Second" to be bound once."""
        if key == "Second" and key in self:
            raise TypeError("Second may only be bound once")
        super().__setitem__(key, value)


class CheckNamespaceMeta(type):
    """Metaclass that requires its two nested classes to arrive in the same form."""

    @classmethod
    def __prepare__(mcs, name, bases, **kwargs):
        """Return the custom namespace."""
        return RejectSecondRewrite()

    def __new__(mcs, name, bases, ns, **kwargs):
        """Build the class, checking the namespace is consistent."""
        if type(ns["First"]) is not type(ns["Second"]):
            raise ValueError("mixed namespace representations")
        return super().__new__(mcs, name, bases, dict(ns))


NESTED_REJECT_SOURCE = """
class Outer(metaclass=CheckNamespaceMeta):
    class First:
        pass
    class Second:
        pass

res = Outer.__name__
"""


async def test_nested_class_prepare_dict_subclass(hass):
    """Test that a custom __prepare__ namespace is left as it is and the class still builds."""
    hass.data[DOMAIN] = {CONFIG_ENTRY: MockConfigEntry(domain=DOMAIN, data={CONF_ALLOW_ALL_IMPORTS: True})}
    Function.init(hass)
    State.init(hass)
    State.register_functions()
    TrigTime.init(hass)
    DecoratorRegistry.init(hass)

    expected_ns: dict = {"CheckNamespaceMeta": CheckNamespaceMeta}
    exec(NESTED_REJECT_SOURCE, expected_ns)  # pylint: disable=exec-used
    global_ctx = GlobalContext(
        "test", global_sym_table={"CheckNamespaceMeta": CheckNamespaceMeta}, manager=GlobalContextMgr
    )
    ast = AstEval("test", global_ctx=global_ctx)
    Function.install_ast_funcs(ast)
    ast.parse(NESTED_REJECT_SOURCE)
    try:
        await ast.eval()
        assert global_ctx.global_sym_table["res"] == expected_ns["res"] == "Outer"
    finally:
        await Function.waiter_sync()
        await Function.waiter_stop()
        await Function.reaper_stop()


GENEXP_TESTS = [
    "result = sum(x * x for x in range(5))",
    """
seen = []
def check(x):
    seen.append(x)
    return x > 1
result = [any(check(x) for x in range(10)), all(check(x) for x in range(10)), seen]
""",
    "result = [min(x % 4 for x in range(1, 9)), max((x for x in range(5)), key=lambda v: -v), sorted(x % 3 for x in range(6))]",
    "result = [list(zip((x for x in 'abc'), (y * 2 for y in range(3)))), list(enumerate(x for x in 'ab'))]",
    "result = [dict((k, k * 2) for k in range(3)), set(x % 2 for x in range(5)), tuple(x for x in 'ab')]",
    "result = ','.join(str(x) for x in range(3)) + '-'.join(f'{x}{y}' for x in 'ab' for y in '12')",
    "result = [next((x for x in [1, 2, 3] if x > 5), None), next(x for x in [1, 2, 3] if x > 1)]",
    "import itertools\nresult = next(x for x in itertools.count() if x > 100)",
    "result = list((x, y) for x in range(4) if x if x != 2 for y in range(x) if y != 1)",
    "result = list(a + sum(b) for a, *b in [(1, 2, 3), (4,)])",
    "x = 'outer'\ng = list(x for x in range(3))\nresult = [x, g]",
    "def f(x):\n    return [list(x for x in range(3)), x]\nresult = f('arg')",
    "def make():\n    n = 10\n    return (i + n for i in range(3))\nresult = list(make())",
    "def f(x):\n    return x * k\nk = 1\ng = (f(x) for x in [1, 2])\nk = 5\nresult = list(g)",
    "lst = [1, 2]\ng = (x for x in lst)\nlst = [9]\nresult = list(g)",
    """
def outer():
    k = 3
    def inner():
        return (x * k for x in range(3))
    g = inner()
    k = 10
    return [list(g), sum(x * k for x in range(3))]
result = outer()
""",
    "r = []\nfor v in (x + 1 for x in range(3)):\n    r.append(v)\nresult = [r, [v * 2 for v in (x for x in range(3) if x != 1)]]",
    "result = [sum(y for y in (x * 2 for x in range(4))), sum(sum(y for y in range(x)) for x in range(5))]",
    "g = (x for x in range(2))\nresult = [list(g), list(g)]",
    "g = (x * 10 for x in range(3))\na = next(g)\nx = 'other'\nb = next(g)\nresult = [a, b, x, list(g)]",
    """
x = 'keep'
g = (1 / x for x in [1, 0, 2])
result = [next(g)]
try:
    next(g)
except ZeroDivisionError as exc:
    result.append(str(exc))
result += [list(g), x]
""",
    """
it = iter([])
try:
    list(next(it) for x in range(3))
except RuntimeError:
    result = "RuntimeError"
""",
    """
def gen_self():
    global g
    g = (next(g) for _ in range(2))
    return g
try:
    list(gen_self())
except ValueError as exc:
    result = str(exc)
""",
    "try:\n    (x for x in 5)\nexcept TypeError as exc:\n    result = str(exc)",
    """
g = (x for x in range(4))
result = [type(g).__name__, repr(g).startswith("<generator object <genexpr> at"), iter(g) is g]
result += [next(g), g.send(None)]
g.close()
result.append(list(g))
""",
    """
class Foo:
    def __init__(self, vals):
        self.vals = vals
    def total(self):
        return sum(v for v in self.vals)
result = [Foo([1, 2, 3]).total(), max(foo.total() for foo in [Foo([1]), Foo([4, 5])])]
""",
    """
class Foo:
    vals = [1, 2, 3]
    total = sum(v for v in vals)
result = [Foo.total, hasattr(Foo, "v")]
""",
    """
found = any((y := x) > 2 for x in range(10))
def func():
    found = any((z := x) > 3 for x in range(10))
    return [found, z]
result = [found, y, func()]
""",
    """
data = {"a": [1, 2], "b": [3]}
def func(*args):
    return args
result = [dict((k, sum(v for v in vs)) for k, vs in data.items()), func(*(x * 2 for x in range(3)))]
""",
    "result = eval('sum(x for x in range(4))')",
]


async def test_generator_expressions_match_python(hass):
    """Test that generator expressions are lazy and give the same results as CPython."""
    hass.data[DOMAIN] = {CONFIG_ENTRY: MockConfigEntry(domain=DOMAIN, data={CONF_ALLOW_ALL_IMPORTS: True})}
    Function.init(hass)
    State.init(hass)
    State.register_functions()
    TrigTime.init(hass)

    for source in GENEXP_TESTS:
        expected_ns = {}
        exec(compile(source, "test", "exec"), expected_ns)  # pylint: disable=exec-used
        global_ctx = GlobalContext("test", global_sym_table={}, manager=GlobalContextMgr)
        ast = AstEval("test", global_ctx=global_ctx)
        Function.install_ast_funcs(ast)
        ast.parse(source)
        await ast.eval()
        assert global_ctx.global_sym_table["result"] == expected_ns["result"], source
        assert ast.sym_table is ast.global_sym_table and not ast.sym_table_stack, source

    await Function.waiter_sync()
    await Function.waiter_stop()
    await Function.reaper_stop()


GENEXP_WAIT_TESTS = [
    "result = sum(task.sleep(0) or x for x in range(3))",
    "def slow(x):\n    task.sleep(0.01)\n    return x\nresult = list(slow(x) for x in range(3))",
    """
import asyncio
async def slow(x):
    await asyncio.sleep(0)
    return x
result = any(slow(x) for x in range(3))
""",
    """
def slow(x):
    try:
        task.sleep(0.01)
    finally:
        task.sleep(0)
    return x
result = list(slow(x) for x in range(3))
""",
]


async def test_generator_expression_errors(hass):
    """Test the errors for a generator expression element that waits, or iteration in another thread."""
    hass.data[DOMAIN] = {CONFIG_ENTRY: MockConfigEntry(domain=DOMAIN, data={CONF_ALLOW_ALL_IMPORTS: True})}
    Function.init(hass)
    State.init(hass)
    State.register_functions()
    TrigTime.init(hass)

    global_ctx = GlobalContext("test", global_sym_table={}, manager=GlobalContextMgr)
    ast = AstEval("test", global_ctx=global_ctx)
    Function.install_ast_funcs(ast)
    for source in GENEXP_WAIT_TESTS:
        ast.parse("x = 'keep'\n" + source)
        with pytest.raises(RuntimeError, match="generator expression element can't wait"):
            await ast.eval()
        assert "result" not in global_ctx.global_sym_table, source
        assert ast.sym_table is ast.global_sym_table and not ast.sym_table_stack, source
        # the generator's loop variable doesn't replace the module's, and later code runs normally
        ast.parse("result = [x, sum(x for x in range(4))]")
        await ast.eval()
        assert global_ctx.global_sym_table.pop("result") == ["keep", 6], source

    ast.parse("task.executor(list, (x for x in range(3)))")
    with pytest.raises(RuntimeError, match="can only be iterated in the event loop"):
        await ast.eval()

    await Function.waiter_sync()
    await Function.waiter_stop()
    await Function.reaper_stop()


#
# each source sets result; pyscript_compile is a no-op decorator when run in CPython
#
CLASS_INIT_SUPER_SOURCES = [
    # zero-argument and two-argument super() in methods and __init__
    """
class B:
    def __init__(self, x):
        self.x = x
    def f(self):
        return "B"
class C(B):
    def __init__(self, x, y):
        super().__init__(x)
        self.y = y
    def f(self):
        return "C" + super().f()
class D(B):
    def __init__(self, x, y):
        super(D, self).__init__(x)
        self.y = y
    def f(self):
        return "D" + super(D, self).f()
c, d = C(1, 2), D(3, 4)
result = [c.x, c.y, c.f(), d.x, d.y, d.f()]
""",
    # Base.__init__(self, ...), and calling __init__ again on an instance
    """
class B:
    def __init__(self, x, k=0):
        self.x = x
        self.k = k
class C(B):
    def __init__(self, x):
        B.__init__(self, x, k=5)
c = C(1)
c.__init__(7)
result = [c.x, c.k]
""",
    # a subclass without __init__ uses the first one along its parents
    """
class A:
    def __init__(self, *args, k=1, **kwargs):
        self.args, self.k, self.kwargs = args, k, kwargs
class B(A):
    pass
class C(B):
    pass
c = C(1, 2, k=3, z=4)
result = [c.args, c.k, c.kwargs, type(c).__name__, isinstance(c, A)]
""",
    """
class A:
    def __init__(self):
        self.a = 1
class B(A):
    def __init__(self):
        super().__init__()
        self.b = 2
class C(B):
    pass
c = C()
result = [c.a, c.b]
""",
    # a native base, with and without an __init__ of our own
    """
class D(dict):
    pass
class E(dict):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.extra = 1
e = E([("a", 1)], b=2)
result = [D(a=1), dict(e), e.extra]
""",
    # custom exceptions keep their message and args
    """
class E1(Exception):
    pass
class E2(Exception):
    def __init__(self, msg, code):
        super().__init__(msg)
        self.code = code
class E3(E2):
    pass
class E4(Exception):
    def __init__(self, msg):
        self.msg = msg
class E5(Exception):
    def __init__(self):
        super().__init__("default")
result = []
for cls, args in [[E1, ["boom", 1]], [E2, ["bad", 3]], [E3, ["worse", 4]], [E4, ["hi"]]]:
    try:
        raise cls(*args)
    except Exception as exc:
        result.append([type(exc).__name__, str(exc), exc.args, getattr(exc, "code", None)])
try:
    raise E5
except E5 as exc:
    result.append([str(exc), exc.args])
""",
    # logging.Handler subclasses
    """
import logging
class H1(logging.Handler):
    def __init__(self, tag):
        super().__init__()
        self.tag = tag
class H2(logging.Handler):
    def __init__(self, tag):
        logging.Handler.__init__(self, level=5)
        self.tag = tag
class H3(logging.Handler):
    pass
h1, h2, h3 = H1("t1"), H2("t2"), H3(10)
result = [h1.tag, h1.level, h2.tag, h2.level, h3.level, hasattr(h1, "lock"), hasattr(h3, "filters")]
""",
    # threading.Thread subclasses
    """
import threading
class T1(threading.Thread):
    def __init__(self, n):
        super().__init__(name=n, daemon=True)
        self.n = n
class T2(threading.Thread):
    pass
t1, t2 = T1("x"), T2(name="y", daemon=True)
result = [t1.name, t1.daemon, t1.n, t2.name, t2.daemon, t1.is_alive()]
""",
    # str and namedtuple bases, which need the arguments in __new__
    """
from collections import namedtuple
class S1(str):
    def shout(self):
        return self.upper()
class S2(str):
    def __init__(self, v):
        self.extra = len(v)
class P1(namedtuple("P", "a b")):
    def s(self):
        return self.a + self.b
class P2(P1):
    pass
class P3(namedtuple("P", "a b")):
    def __init__(self, a, b):
        self.total = a + b
s2, p3 = S2("hi"), P3(1, 2)
result = [S1("hi").shout(), s2, s2.extra, P1(1, 2).s(), P2(3, b=4).s(), tuple(p3), p3.total]
""",
    # diamond inheritance and mixins follow the mro
    """
log = []
class A:
    def __init__(self):
        log.append("A")
class B(A):
    def __init__(self):
        log.append("B")
        super().__init__()
class C(A):
    def __init__(self):
        log.append("C")
        super().__init__()
class D(B, C):
    def __init__(self):
        log.append("D")
        super().__init__()
class E(B, C):
    pass
class F(B, C):
    def __init__(self):
        log.append("F")
        super(B, self).__init__()
D()
E()
F()
result = [log, [k.__name__ for k in D.__mro__]]
""",
    """
class Hello:
    def hello(self):
        return "hi " + self.name
class Named:
    def __init__(self, name, **kwargs):
        super().__init__(**kwargs)
        self.name = name
class N(Hello, Named, dict):
    pass
n = N("bob", a=1)
result = [n.hello(), dict(n)]
""",
    # metaclasses: one without __call__, with a pyscript __new__ or __init__, and with a compiled __call__
    """
class Meta1(type):
    pass
class Reg(type):
    names = []
    def __init__(cls, name, bases, ns):
        super().__init__(name, bases, ns)
        Reg.names.append(name)
class Meta2(type):
    def __new__(mcs, name, bases, ns):
        cls = super().__new__(mcs, name, bases, ns)
        cls.tag = name.lower()
        return cls
class Meta3(type):
    @pyscript_compile
    def __call__(cls, *args, **kwargs):
        inst = type.__call__(cls, *args, **kwargs)
        inst.called = True
        return inst
class A(metaclass=Meta1):
    def __init__(self, v):
        self.v = v
class B(metaclass=Meta2):
    def __init__(self, v):
        self.v = v
class C(metaclass=Meta3):
    def __init__(self, v):
        self.v = v
class D(metaclass=Meta3):
    pass
class E(metaclass=Reg):
    pass
class F(E):
    pass
c = C(3)
result = [A(1).v, B(2).v, B.tag, c.v, c.called, D().called, Reg.names, type(Reg("G", (), {})).__name__, Reg.names]
""",
    """
from abc import ABC
class B(ABC):
    def __init__(self, v):
        self.v = v
class C(B):
    def f(self):
        return self.v
result = [C(4).f(), isinstance(C(5), B)]
""",
    # a pyscript __new__ alone, with __init__, and returning another type
    """
class A:
    def __new__(cls, *args):
        inst = super().__new__(cls)
        inst.made = args
        return inst
class B:
    def __new__(cls, v):
        inst = super().__new__(cls)
        inst.order = ["new"]
        return inst
    def __init__(self, v):
        self.order.append("init")
        self.v = v
class C(B):
    pass
class D:
    def __new__(cls, v):
        return v
    def __init__(self, v):
        raise ValueError("not called")
class S(str):
    def __new__(cls, v):
        return super().__new__(cls, v.upper())
class T:
    @staticmethod
    def __new__(cls, v):
        inst = super().__new__(cls)
        inst.v = v
        return inst
b, c = B(1), C(2)
result = [A(1, 2).made, b.order, b.v, c.order, c.v, type(c).__name__, D(5), S("hi"), T(6).v]
""",
    # compiled __init__s, also inherited and called through super()
    """
class A:
    @pyscript_compile
    def __init__(self, v):
        self.v = v
class B(A):
    pass
class C(A):
    def __init__(self, v):
        super().__init__(v + 1)
        A.__init__(self, self.v + 1)
result = [A(1).v, B(2).v, C(3).v]
""",
    # a static __init__, and an __init__ set on the instance
    """
log = []
class A:
    @staticmethod
    def __init__(x):
        log.append(x)
class B(A):
    pass
class C:
    def __init__(self):
        self.x = 1
A(3)
B(4)
A.__init__(5)
c = C()
def reset():
    c.x = 2
c.__init__ = reset
c.__init__()
result = [log, c.x, type(C()).__name__]
""",
    # __init__ must return None
    """
class A:
    def __init__(self):
        return 3
try:
    A()
    result = "no error"
except TypeError as exc:
    result = str(exc)
""",
    # super() in a function defined in a method, in a method with a closure, and in a decorated class
    """
class B:
    def f(self):
        return "B"
class C(B):
    def f(self):
        def inner(me):
            return super().f()
        return "C" + inner(self)
    def g(self):
        def get():
            return self
        return super().f() + type(get()).__name__
def deco(cls):
    class Wrapped(cls):
        pass
    return Wrapped
@deco
class D(B):
    def f(self):
        return "D" + super().f()
result = [C().f(), C().g(), D().f(), type(D()).__name__]
""",
    # classes defined inside a function
    """
def make(base_v):
    class B:
        def __init__(self, v):
            self.v = v + base_v
    class C(B):
        def __init__(self, v):
            super().__init__(v)
            self.w = self.v * 2
    class D(C):
        pass
    return D
D = make(10)
d = D(1)
result = [d.v, d.w]
""",
    # dataclasses keep their generated __init__
    """
from dataclasses import dataclass, field
@dataclass
class P:
    x: int
    y: int = 2
    z: list = field(default_factory=list)
@dataclass
class Q:
    x: int
    @pyscript_compile
    def __post_init__(self):
        self.double = self.x * 2
class R(P):
    def total(self):
        return self.x + self.y
result = [repr(P(1)), Q(3).double, R(3, 4).total()]
""",
    # errors from a zero-argument super(), as in Python
    """
def f(x):
    return super()
class A:
    def f():
        return super()
    def g(self):
        del self
        return super()
    def h(self):
        return super()
    try:
        h(1)
    except RuntimeError as exc:
        empty = str(exc)
result = [A.empty]
for func, args in [[f, [1]], [A.f, []], [A.g, [A()]]]:
    try:
        func(*args)
    except RuntimeError as exc:
        result.append(str(exc))
""",
    # zero-argument super() in a generator expression's element uses the generator's iterator, as in Python
    """
class B:
    def f(self):
        return self.n
class C(B):
    def __init__(self, n):
        self.n = n
        self.done = False
    @pyscript_compile
    def __iter__(self):
        return self
    @pyscript_compile
    def __next__(self):
        if self.done:
            raise StopIteration
        self.done = True
        return 0
    def values(self, other):
        return (super().f() for _ in other)
    def nested(self, other):
        # the inner generator's iterator is a list iterator, but super() is in the outer element
        return list(list(super().f() for _ in other) for _ in other)
    def outermost(self, other):
        # only the outermost iterable is evaluated in the method, so super() there is the method's
        return list(0 for _ in [super().f()] if _ == 10 for _ in other)
    def cond(self, other):
        return list(1 for _ in other if super().f() == 20)
    def plain(self):
        return list(super().f() for _ in range(2))
    def explicit(self):
        return list(super(C, self).f() for _ in range(2))
result = [list(C(10).values(C(20))), list(C(10).cond(C(20))), C(10).outermost(C(1))]
c = C(10)
for func, args in [[c.plain, []], [c.explicit, []], [c.nested, [C(1)]]]:
    try:
        result.append(func(*args))
    except (TypeError, RuntimeError) as exc:
        result.append(str(exc))
""",
    """
ONE = [1]  # a name, since Python makes a tuple of a literal list
def g():
    return (super().f() for _ in ONE)
class B:
    def f(self):
        return 1
class C(B):
    def values(self):
        return (super().f() for _ in ONE)
    def values2(self):
        return (list(y for y in [super().f()]) for _ in ONE)
result = []
for func in [g, C().values, C().values2]:
    try:
        result.append(list(func()))
    except (TypeError, RuntimeError) as exc:
        result.append(str(exc))
""",
    # a method that uses super() and is called from a generator expression's element, condition or nested generator
    """
class B:
    def f(self):
        return "B" + str(self.n)
    def inherited(self):
        return list(self.g() for _ in range(2))
class C(B):
    def __init__(self, n):
        self.n = n
    def g(self):
        return super().f()
    def g2(self):
        return super(C, self).f()
    def element(self):
        return list(self.g() for _ in range(2))
    def element2(self):
        return list(self.g2() for _ in range(2))
    def cond(self):
        return list(i for i in range(3) if self.g() == "B1" and i != 1)
    def nested(self):
        return list(list(self.g() for _ in range(2)) for _ in range(2))
    def both(self):
        return list((self.g(), self.g2()) for _ in range(2))
c = C(1)
result = [c.element(), c.element2(), c.cond(), c.nested(), c.both(), c.inherited()]
""",
    # __new__ returning an instance of another class: __init__ runs once, only if it is an instance of the class
    """
calls = []
class Other:
    def __init__(self, *args):
        calls.append(["Other", args])
class OtherInherit(Other):
    pass
def make(*args):
    return args
class Sub:
    def __new__(cls, *args):
        return object.__new__(Sub2)
    def __init__(self, *args):
        calls.append(["Sub", args])
class Sub2(Sub):
    def __init__(self, *args):
        calls.append(["Sub2", args])
class A:
    kind = Other
    def __new__(cls, *args):
        return cls.kind(*args)
    def __init__(self, *args):
        calls.append(["A", args])
result = []
for kind in [Other, OtherInherit, make]:
    A.kind = kind
    calls.clear()
    obj = A(1, 2)
    result.append([type(obj).__name__, obj if kind is make else None, calls[:]])
calls.clear()
obj = Sub(3)
result.append([type(obj).__name__, None, calls[:]])
""",
]


async def test_class_init_super_match_python(hass, caplog):
    """Test inherited __init__, Base.__init__ and super() in classes, compared with CPython."""
    hass.data[DOMAIN] = {CONFIG_ENTRY: MockConfigEntry(domain=DOMAIN, data={CONF_ALLOW_ALL_IMPORTS: True})}
    Function.init(hass)
    State.init(hass)
    State.register_functions()
    TrigTime.init(hass)
    DecoratorRegistry.init(hass)

    # debug logging formats the call arguments, which can include an instance whose __init__ hasn't run
    for level in [logging.INFO, logging.DEBUG]:
        caplog.set_level(level, logger="custom_components.pyscript.eval")
        for source in CLASS_INIT_SUPER_SOURCES:
            expected_ns = {"pyscript_compile": lambda func: func}
            exec(compile(source, "test", "exec"), expected_ns)  # pylint: disable=exec-used

            global_ctx = GlobalContext("test", global_sym_table={}, manager=GlobalContextMgr)
            ast = AstEval("test", global_ctx=global_ctx)
            Function.install_ast_funcs(ast)
            ast.parse(source + "result\n")
            assert await ast.eval() == expected_ns["result"], source

    await Function.waiter_sync()
    await Function.waiter_stop()
    await Function.reaper_stop()


PROPERTY_CLASSMETHOD_SOURCES = [
    "class A:\n    def __init__(self): self._x = 3\n    @property\n    def x(self): return self._x * 2\nresult = A().x",
    "class A:\n    def __init__(self): self._x = 3\n    @property\n    def x(self): return self._x\n    @x.setter\n    def x(self, v): self._x = v + 1\na = A(); a.x = 10\nresult = a.x",
    "class A:\n    n = 5\n    @classmethod\n    def make(cls, k): return cls.n + k\nresult = (A.make(1), A().make(2))",
    "class A:\n    def __init__(self, v): self.v = v\n    @classmethod\n    def build(cls): return cls(7)\nresult = A.build().v",
    "class A:\n    @staticmethod\n    def f(a, b): return a + b\nresult = (A.f(1, 2), A().f(3, 4))",
    "class B:\n    @property\n    def x(self): return 1\nclass C(B): pass\nresult = C().x",
    "class A:\n    def __init__(self): self._x = 1\n    @property\n    def x(self): return self._x\n    @x.setter\n    def x(self, v): self._x = v * 10\na = A(); a.x += 1\nresult = a._x",
    "class A:\n    @property\n    def x(self): return 1\ntry:\n    A().x = 3\n    result = 'set'\nexcept AttributeError as e:\n    result = 'AttributeError'",
    "class A:\n    @property\n    def x(self): return 1\nresult = type(A.x).__name__",
    "class A:\n    def _get(self): return 4\n    x = property(_get)\nresult = A().x",
    "class B:\n    @property\n    def x(self): return 1\nclass C(B):\n    @property\n    def x(self): return 2\nresult = (B().x, C().x)",
    "class A:\n    @property\n    def x(self): return 'prop'\na = A()\na.__dict__['x'] = 'dict'\nresult = a.x",
    "class A:\n    def __init__(self): self.v = 2\n    def dbl(self): return self.v * 2\n    @property\n    def x(self): return self.dbl() + 1\nresult = A().x",
    "class A:\n    @property\n    def x(self): raise AttributeError('nope')\ntry:\n    A().x\n    result = 'ok'\nexcept AttributeError:\n    result = 'AttributeError'",
    "class B:\n    @classmethod\n    def name(cls): return cls.__name__\nclass C(B): pass\nresult = (B.name(), C.name(), C().name())",
    "class B:\n    @classmethod\n    def f(cls, a, *, k=0): return (cls.__name__, a, k)\nresult = B.f(1, k=2)",
    "class A:\n    @pyscript_compile\n    def _g(self): return 5\n    x = property(_g)\nresult = A().x",
    "class B:\n    def __init__(self): self._v = 0\n    @property\n    def v(self): return self._v\n    @v.setter\n    def v(self, x): self._v = x * 2\nclass A:\n    def __init__(self): self.b = B()\na = A(); a.b.v = 5; a.b.v += 1\nresult = (a.b.v, a.b._v)",
    "class B:\n    @property\n    def v(self): return 7\nclass A:\n    @property\n    def b(self): return B()\nresult = A().b.v",
    "class B:\n    def __init__(self): self._v = 0\n    @property\n    def v(self): return self._v\n    @v.setter\n    def v(self, x): self._v = x + 1\nclass C(B): pass\nc = C(); c.v, y = 3, 4\nresult = (c.v, y)",
    "class M(type):\n    @property\n    def p(cls): return cls.__name__\nclass A(metaclass=M): pass\nresult = A.p",
    "class A:\n    @pyscript_compile\n    def __getattribute__(self, key):\n        if key == 'x': return 42\n        return object.__getattribute__(self, key)\n    @property\n    def x(self): return 1\nresult = A().x",
    "class A:\n    def __init__(self): object.__setattr__(self, 'log', [])\n    @pyscript_compile\n    def __setattr__(self, key, val):\n        self.log.append((key, val))\n    @property\n    def x(self): return 1\n    @x.setter\n    def x(self, v): raise ValueError('setter used')\na = A(); a.x = 7\nresult = a.log",
    "class A:\n    @pyscript_compile\n    def __getattr__(self, key): return 42\n    @property\n    def x(self): raise AttributeError('missing')\nresult = A().x",
    "class A:\n    @property\n    def x(self): raise AttributeError('missing')\ntry:\n    A().x\n    result = 'ok'\nexcept AttributeError as e:\n    result = str(e)",
    "class P(property):\n    @pyscript_compile\n    def __get__(self, instance, owner=None): return 42\nclass A:\n    @P\n    def x(self): return 1\nresult = A().x",
    "class P(property):\n    @pyscript_compile\n    def __set__(self, instance, value): instance.log = ('set', value)\nclass A:\n    @P\n    def x(self): return 1\n    @x.setter\n    def x(self, v): raise ValueError('setter used')\na = A(); a.x = 7\nresult = a.log",
    "class A:\n    @staticmethod\n    def __getattr__(key): return 'static ' + key\n    @property\n    def x(self): raise AttributeError('missing')\nresult = A().x",
    "class A:\n    @classmethod\n    def __getattr__(cls, key): return cls.__name__ + key\n    @property\n    def x(self): raise AttributeError('missing')\nresult = A().x",
    "class A:\n    def __getattr__(self, key): return type(self).__name__ + key\n    @property\n    def x(self): raise AttributeError('missing')\nresult = A().x",
    "class A:\n    @classmethod\n    def f(cls): return 1\n    def g(self): return self.f() + A.f() + type(self).f()\nresult = A().g()",
]


async def test_property_classmethod_match_python(hass):
    """Test @property and @classmethod on pyscript functions, compared with CPython."""
    hass.data[DOMAIN] = {CONFIG_ENTRY: MockConfigEntry(domain=DOMAIN, data={CONF_ALLOW_ALL_IMPORTS: True})}
    DecoratorRegistry.init(hass)
    Function.init(hass)
    State.init(hass)
    State.register_functions()
    TrigTime.init(hass)

    for source in PROPERTY_CLASSMETHOD_SOURCES:
        expected_ns = {"pyscript_compile": lambda func: func}
        exec(compile(source, "test", "exec"), expected_ns)  # pylint: disable=exec-used
        global_ctx = GlobalContext("test", global_sym_table={}, manager=GlobalContextMgr)
        ast = AstEval("test", global_ctx=global_ctx)
        Function.install_ast_funcs(ast)
        ast.parse(source + "\nresult\n")
        assert await ast.eval() == expected_ns["result"], source

    await Function.waiter_sync()
    await Function.waiter_stop()
    await Function.reaper_stop()


PROPERTY_DELETER_SOURCES = [
    # the deleter runs, with its side effects
    """
class A:
    def __init__(self):
        self._x = 1
        self.log = []
    @property
    def x(self):
        return self._x
    @x.deleter
    def x(self):
        self.log.append("del")
        del self._x
a = A()
del a.x
result = (a.log, "_x" in a.__dict__)
""",
    # a deleter used inside a function, then the getter and setter still work
    """
class A:
    def __init__(self):
        self._x = 1
    @property
    def x(self):
        return self._x
    @x.setter
    def x(self, v):
        self._x = v
    @x.deleter
    def x(self):
        self._x = None
def f():
    a = A()
    del a.x
    first = a.x
    a.x = 5
    return (first, a.x)
result = f()
""",
    # a property with no deleter raises the same AttributeError as Python
    """
class A:
    @property
    def x(self):
        return 1
try:
    del A().x
    result = "deleted"
except AttributeError as exc:
    result = str(exc)
""",
    # property(fget, fset, fdel)
    """
class A:
    def __init__(self):
        self.log = []
    def _get(self):
        return 1
    def _set(self, v):
        self.log.append(("set", v))
    def _del(self):
        self.log.append("del")
    x = property(_get, _set, _del)
a = A()
a.x = 2
del a.x
result = (a.x, a.log)
""",
    # a deleter inherited from a base class
    """
class B:
    def __init__(self):
        self.log = []
    @property
    def x(self):
        return 1
    @x.deleter
    def x(self):
        self.log.append(type(self).__name__)
class C(B):
    pass
c = C()
del c.x
result = c.log
""",
    # a deleter that raises: the later targets aren't deleted
    """
class A:
    @property
    def x(self):
        return 1
    @x.deleter
    def x(self):
        raise ValueError("no delete")
a = A()
b = [1, 2]
try:
    del a.x, b[0]
    result = "deleted"
except ValueError as exc:
    result = (str(exc), b)
""",
    # the targets are deleted in order
    """
log = []
class A:
    @property
    def x(self):
        return 1
    @x.deleter
    def x(self):
        log.append(list(b))
a = A()
b = [1, 2, 3]
del a.x, b[0]
del b[0], a.x
result = (log, b)
""",
    # a class with its own __delattr__ gets it called, not the deleter
    """
class A:
    def __init__(self):
        object.__setattr__(self, "log", [])
    @pyscript_compile
    def __delattr__(self, key):
        self.log.append(key)
    @property
    def x(self):
        return 1
    @x.deleter
    def x(self):
        raise ValueError("deleter used")
a = A()
del a.x
result = a.log
""",
    # a native descriptor with __delete__
    """
class D:
    @pyscript_compile
    def __get__(self, inst, owner=None):
        return 1
    @pyscript_compile
    def __delete__(self, inst):
        inst.log.append("delete")
class A:
    x = D()
    def __init__(self):
        self.log = []
a = A()
del a.x
result = a.log
""",
    # a native property's deleter
    """
class A:
    def __init__(self):
        self.log = []
    @pyscript_compile
    def _get(self):
        return 1
    @pyscript_compile
    def _del(self):
        self.log.append("native del")
    x = property(_get, None, _del)
a = A()
del a.x
result = a.log
""",
    # a plain attribute, and one that doesn't exist
    """
class A:
    pass
a = A()
a.y = 1
del a.y
result = ["y" in a.__dict__]
try:
    del a.y
except AttributeError as exc:
    result.append(str(exc))
""",
    # a deleter on a metaclass property
    """
class M(type):
    @property
    def p(cls):
        return 1
    @p.deleter
    def p(cls):
        cls.log.append("del " + cls.__name__)
class A(metaclass=M):
    log = []
del A.p
result = A.log
""",
]


async def test_property_deleter_match_python(hass):
    """Test del on a pyscript property deleter, compared with CPython."""
    hass.data[DOMAIN] = {CONFIG_ENTRY: MockConfigEntry(domain=DOMAIN, data={CONF_ALLOW_ALL_IMPORTS: True})}
    DecoratorRegistry.init(hass)
    Function.init(hass)
    State.init(hass)
    State.register_functions()
    TrigTime.init(hass)

    for source in PROPERTY_DELETER_SOURCES:
        expected_ns = {"pyscript_compile": lambda func: func}
        exec(compile(source, "test", "exec"), expected_ns)  # pylint: disable=exec-used
        global_ctx = GlobalContext("test", global_sym_table={}, manager=GlobalContextMgr)
        ast = AstEval("test", global_ctx=global_ctx)
        Function.install_ast_funcs(ast)
        ast.parse(source + "result\n")
        # a deleter that Python called itself would leave a coroutine that's never awaited
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            assert await ast.eval() == expected_ns["result"], source
            gc.collect()
        assert not [w for w in caught if "never awaited" in str(w.message)], source

    await Function.waiter_sync()
    await Function.waiter_stop()
    await Function.reaper_stop()


CLASSMETHOD_SUPER_SOURCES = [
    # super() in a classmethod, called on the class and on an instance
    """
class B:
    @classmethod
    def make(cls):
        return cls.__name__ + "-B"
class C(B):
    @classmethod
    def make(cls):
        return super().make() + "-C"
result = (C.make(), C().make())
""",
    # a chain of three classes
    """
class A:
    @classmethod
    def make(cls, n):
        return [("A", cls.__name__, n)]
class B(A):
    @classmethod
    def make(cls, n):
        return super().make(n + 1) + [("B", cls.__name__, n)]
class C(B):
    @classmethod
    def make(cls, n):
        return super().make(n + 1) + [("C", cls.__name__, n)]
result = (C.make(0), B.make(0), C().make(5))
""",
    # super(C, cls) in a classmethod, and from a subclass
    """
class B:
    @classmethod
    def make(cls):
        return cls.__name__
class C(B):
    @classmethod
    def make(cls):
        return "C:" + super(C, cls).make()
class D(C):
    pass
result = (C.make(), D.make(), D().make())
""",
    # super(C, cls) in a chain of three classes
    """
class A:
    @classmethod
    def make(cls, k=0):
        return ["A", cls.__name__, k]
class B(A):
    @classmethod
    def make(cls, k=0):
        return super(B, cls).make(k=k + 1) + ["B"]
class C(B):
    @classmethod
    def make(cls, k=0):
        return super(C, cls).make(k=k + 1) + ["C"]
result = (C.make(), C().make(k=10))
""",
    # an alternate constructor that calls the base class's through super()
    """
class B:
    def __init__(self, v):
        self.v = v
    @classmethod
    def build(cls, v):
        return cls(v)
class C(B):
    def __init__(self, v):
        super().__init__(v * 2)
    @classmethod
    def build(cls, v):
        return super().build(v + 1)
c = C.build(1)
result = (type(c).__name__, c.v)
""",
    # a class attribute through super() in a classmethod
    """
class B:
    n = 1
class C(B):
    n = 2
    @classmethod
    def f(cls):
        return (cls.n, super().n)
result = (C.f(), C().f())
""",
]


async def test_classmethod_super_match_python(hass):
    """Test super() inside a classmethod, compared with CPython."""
    hass.data[DOMAIN] = {CONFIG_ENTRY: MockConfigEntry(domain=DOMAIN, data={CONF_ALLOW_ALL_IMPORTS: True})}
    DecoratorRegistry.init(hass)
    Function.init(hass)
    State.init(hass)
    State.register_functions()
    TrigTime.init(hass)

    for source in CLASSMETHOD_SUPER_SOURCES:
        expected_ns = {}
        exec(compile(source, "test", "exec"), expected_ns)  # pylint: disable=exec-used
        global_ctx = GlobalContext("test", global_sym_table={}, manager=GlobalContextMgr)
        ast = AstEval("test", global_ctx=global_ctx)
        Function.install_ast_funcs(ast)
        ast.parse(source + "result\n")
        assert await ast.eval() == expected_ns["result"], source

    await Function.waiter_sync()
    await Function.waiter_stop()
    await Function.reaper_stop()


SUPER_PROPERTY_SOURCES = [
    # super().prop and super(C, self).prop in a method and in an overriding getter
    """
class B:
    def __init__(self):
        self._v = 3
    @property
    def x(self):
        return self._v * 2
class C(B):
    @property
    def x(self):
        return super().x + 1
    def f(self):
        return (super().x, super(C, self).x)
    @property
    def y(self):
        return super(C, self).x * 10
c = C()
result = (c.x, c.f(), c.y)
""",
    # a chain of three classes, each extending its parent's property
    """
class A:
    @property
    def label(self):
        return ["A", type(self).__name__]
class B(A):
    @property
    def label(self):
        return super().label + ["B"]
class C(B):
    @property
    def label(self):
        return super().label + ["C"]
result = (A().label, B().label, C().label)
""",
    # a property defined in a mixin, in a diamond-shaped hierarchy
    """
class Base:
    @property
    def name(self):
        return ["Base"]
class Mixin(Base):
    @property
    def name(self):
        return ["Mixin"] + super().name
class Left(Base):
    @property
    def name(self):
        return ["Left"] + super().name
class D(Left, Mixin):
    @property
    def name(self):
        return ["D"] + super().name
result = ([k.__name__ for k in D.__mro__], D().name, super(Left, D()).name)
""",
    # a getter that raises AttributeError: no __getattr__ fallback through super()
    """
class B:
    @property
    def x(self):
        raise AttributeError("missing x")
class C(B):
    def __getattr__(self, key):
        return "fallback " + key
    def f(self):
        try:
            return super().x
        except AttributeError as exc:
            return "AttributeError: " + str(exc)
result = (C().f(), C().x)
""",
    # a compiled getter, reached through super()
    """
class B:
    def __init__(self):
        self.v = 4
    @pyscript_compile
    def _get(self):
        return self.v + 1
    x = property(_get)
class C(B):
    @property
    def x(self):
        return super().x * 2
result = C().x
""",
    # a native property, from a class defined in Python
    """
import fractions
class F(fractions.Fraction):
    @property
    def numerator(self):
        return super().numerator * 10
f = F(3, 4)
result = (f.numerator, f.denominator, super(F, f).numerator)
""",
    # super(C, cls).prop on a class gives the property itself, as in Python
    """
class B:
    @property
    def x(self):
        return 1
class C(B):
    @classmethod
    def f(cls):
        return (super().x is B.__dict__["x"], super(C, cls).x is B.__dict__["x"])
result = (C.f(), C().f(), type(super(C, C).x).__name__)
""",
    # a class with its own __getattribute__: super() doesn't use it
    """
class B:
    @property
    def x(self):
        return "B.x"
class C(B):
    @pyscript_compile
    def __getattribute__(self, key):
        if key == "x":
            return "C.__getattribute__"
        return object.__getattribute__(self, key)
    def f(self):
        return super().x
result = (C().x, C().f())
""",
    # super(C, self).prop in a generator expression and a comprehension
    """
class B:
    @property
    def x(self):
        return 10
class C(B):
    def f(self):
        gen = list(super(C, self).x + i for i in range(3))
        comp = [super(C, self).x + i for i in range(3)]
        zero = [super().x + i for i in range(3)]
        return (gen, comp, zero)
result = C().f()
""",
    # zero-argument super() in a generator expression gives Python's own TypeError
    """
class B:
    @property
    def x(self):
        return 10
class C(B):
    def f(self):
        try:
            return list(super().x for i in range(3))
        except TypeError as exc:
            return str(exc)
result = C().f()
""",
    # a metaclass property read through super(Meta, cls) and super()
    """
class M(type):
    @property
    def p(cls):
        return "M:" + cls.__name__
class N(M):
    @property
    def p(cls):
        return "N+" + super().p
    @property
    def q(cls):
        return super(N, cls).p
class A(metaclass=N):
    pass
result = (A.p, A.q)
""",
    # a property named __class__ on a parent: super().__class__ is still super
    """
class B:
    @property
    def __class__(self):
        return int
class C(B):
    def f(self):
        return super().__class__
result = C().f().__name__
""",
    # a subclass of super with its own __getattribute__ is left to it
    """
class S(super):
    @pyscript_compile
    def __getattribute__(self, key):
        if key == "x":
            return "S.x"
        return super.__getattribute__(self, key)
class B:
    @property
    def x(self):
        return "B.x"
class C(B):
    def f(self):
        return S(C, self).x
result = C().f()
""",
    # a nearer plain attribute or method hides a farther property
    """
class A:
    @property
    def x(self):
        return "A.x"
    @property
    def y(self):
        return "A.y"
class B(A):
    x = "B.x"
    def y(self):
        return "B.y()"
class C(B):
    @property
    def x(self):
        return super().x
    @property
    def y(self):
        return super().y()
result = (C().x, C().y)
""",
    # a property with no getter raises Python's own AttributeError
    """
class B:
    def _set(self, v):
        pass
    x = property(None, _set)
class C(B):
    def f(self):
        try:
            return super().x
        except AttributeError as exc:
            return str(exc)
result = C().f()
""",
    # a property on a subclass of property is left to Python
    """
class P(property):
    @pyscript_compile
    def __get__(self, instance, owner=None):
        return 42
class B:
    @P
    def x(self):
        return 1
class C(B):
    def f(self):
        return super().x
result = C().f()
""",
    # an unbound super(C) has no property
    """
class B:
    @property
    def x(self):
        return 1
class C(B):
    pass
try:
    super(C).x
    result = "found"
except AttributeError as exc:
    result = str(exc)
""",
    # super() after the class's bases change uses the new mro
    """
class A:
    @property
    def x(self):
        return "A"
class B:
    @property
    def x(self):
        return "B"
class C(A):
    def f(self):
        return super().x
c = C()
first = c.f()
C.__bases__ = (B,)
result = (first, c.f())
""",
    # a super object whose __thisclass__ is no longer in the mro keeps Python's error
    """
class A:
    @property
    def x(self):
        return "A"
class B(A):
    pass
class C(B):
    pass
c = C()
s = super(B, c)
C.__bases__ = (A,)
try:
    s.x
    result = "found"
except AttributeError as exc:
    result = str(exc)
""",
    # setting or deleting through super() raises the same AttributeError as Python
    """
class B:
    def __init__(self):
        self._x = 1
    @property
    def x(self):
        return self._x
    @x.setter
    def x(self, v):
        self._x = v
    @x.deleter
    def x(self):
        self._x = None
class C(B):
    def f(self):
        result = []
        try:
            super().x = 5
        except AttributeError as exc:
            result.append(str(exc))
        try:
            del super().x
        except AttributeError as exc:
            result.append(str(exc))
        return result + [self._x]
result = C().f()
""",
    # a parent's setter called directly through super(C, type(self))
    """
class B:
    def __init__(self):
        self._x = 1
    @property
    def x(self):
        return self._x
    @x.setter
    def x(self, v):
        self._x = v * 2
class C(B):
    @property
    def x(self):
        return super().x + 100
    @x.setter
    def x(self, v):
        super(C, type(self)).x.fset(self, v + 1)
c = C()
c.x = 4
result = (c._x, c.x)
""",
    # a parent's attribute named __self_class__ doesn't change what super() searches
    """
class B:
    __self_class__ = "shadowed"
    x = 42
class C(B):
    pass
result = super(C, C()).x
""",
    # a parent's compiled property named __self__ isn't run by super()
    """
class B:
    @pyscript_compile
    def _get(self):
        raise ValueError("__self__ property ran")
    __self__ = property(_get)
    x = 42
class C(B):
    pass
result = super(C, C()).x
""",
    # a parent's compiled property named __thisclass__ isn't run by super()
    """
class B:
    @pyscript_compile
    def _get(self):
        raise ValueError("__thisclass__ property ran")
    __thisclass__ = property(_get)
    x = 42
class C(B):
    pass
result = super(C, C()).x
""",
    # a parent's attribute named __self__ doesn't change the instance the getter gets
    """
class B:
    __self__ = 123
    @property
    def name(self):
        return type(self).__name__
class C(B):
    def f(self):
        return super().name
result = C().f()
""",
    # a metaclass __getattribute__ that blocks __mro__ or __dict__ isn't used by super()
    """
@pyscript_compile
def make(blocked):
    class M(type):
        def __getattribute__(cls, key):
            if key == blocked:
                raise RuntimeError("metaclass read " + key)
            return type.__getattribute__(cls, key)
    class B(metaclass=M):
        x = 42
        def f(self):
            return 43
    class C(B):
        pass
    return super(C, C())
s = make("__mro__")
result = [s.x, s.f()]
s = make("__dict__")
result.append(s.x)
""",
    # a metaclass data descriptor for __mro__ or __dict__ isn't used by super()
    """
@pyscript_compile
def make(blocked):
    def fail(cls):
        raise RuntimeError("metaclass descriptor read " + blocked)
    M = type("M", (type,), {blocked: property(fail)})
    class B(metaclass=M):
        x = 42
    class C(B):
        pass
    return super(C, C())
result = (make("__mro__").x, make("__dict__").x)
""",
    # a native getter whose __class__ is hooked: super() doesn't read it
    """
@pyscript_compile
def make():
    class Getter:
        def __call__(self, obj):
            return 42
        def __getattribute__(self, name):
            if name == "__class__":
                raise RuntimeError("getter __class__ hook called")
            return object.__getattribute__(self, name)
    class B:
        x = property(Getter())
    class C(B):
        pass
    return super(C, C())
result = make().x
""",
]


async def test_super_property_match_python(hass):
    """Test reading a pyscript property through super(), compared with CPython."""
    hass.data[DOMAIN] = {CONFIG_ENTRY: MockConfigEntry(domain=DOMAIN, data={CONF_ALLOW_ALL_IMPORTS: True})}
    DecoratorRegistry.init(hass)
    Function.init(hass)
    State.init(hass)
    State.register_functions()
    TrigTime.init(hass)

    for source in SUPER_PROPERTY_SOURCES:
        expected_ns = {"pyscript_compile": lambda func: func}
        exec(compile(source, "test", "exec"), expected_ns)  # pylint: disable=exec-used
        global_ctx = GlobalContext("test", global_sym_table={}, manager=GlobalContextMgr)
        ast = AstEval("test", global_ctx=global_ctx)
        Function.install_ast_funcs(ast)
        ast.parse(source + "result\n")
        # a getter that Python called itself would leave a coroutine that's never awaited
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            assert await ast.eval() == expected_ns["result"], source
            gc.collect()
        if "def __class__" not in source:
            # isinstance() reads a pyscript __class__ getter as a normal function, which pyscript can't stop
            assert not [w for w in caught if "never awaited" in str(w.message)], source

    await Function.waiter_sync()
    await Function.waiter_stop()
    await Function.reaper_stop()


LAMBDA_TESTS = [
    "def f(x, y=2):\n    return (lambda: x + y)()\nresult = f(3)",
    "def f():\n    n = 5\n    g = lambda v: v * n\n    return [g(1), g(2)]\nresult = f()",
    """
def outer(a):
    b = 10
    def middle(c):
        def inner(d):
            return (lambda: (a, b, c, d))()
        return inner(4)
    return middle(3)
result = outer(1)
""",
    """
def f():
    limit = 1
    g = lambda v: v > limit
    first = list(filter(g, [0, 1, 2, 3]))
    limit = 2
    return [first, list(filter(g, [0, 1, 2, 3]))]
result = f()
""",
    "def make():\n    k = 1\n    g = lambda: k\n    k = 2\n    return g\nresult = make()()",
    "def make(n):\n    return lambda v: v + n\na = make(1)\nb = make(100)\nresult = [a(1), b(1), a(2), make(5)(5)]",
    """
def f():
    x = 1
    def g():
        return lambda: x
    h = g()
    x = 2
    return h()
result = f()
""",
    """
def f(a):
    def g():
        a = 'shadow'
        return lambda: a
    return [g()(), (lambda: a)()]
result = f('param')
""",
    """
def f():
    v = 'outer'
    return [(lambda v: v)('param'), v, (lambda *a, **kw: (a, kw, v))(1, z=2), (lambda a: lambda b: (a, b, v))(1)(2)]
result = f()
""",
    "x = 'global'\ndef f():\n    x = 'local'\n    return (lambda: x)()\nresult = [f(), (lambda: x)()]",
    """
x = 5
def f():
    x = 7
    def g():
        pass
    return [(lambda v=x: v)(), (lambda *, v=x: v)(), (lambda a, b=x, *, c=x + 1: (a, b, c))(0)]
result = f()
""",
    """
def f():
    fs = []
    for i in range(3):
        fs.append(lambda i=i: i)
    return [h() for h in fs] + [(lambda a, b=2, *c, d, e=5, **f: 0).__kwdefaults__]
result = f()
""",
    """
def f(items, d, value):
    return [
        sorted(items, key=lambda i: d[i]),
        max(items, key=lambda i: d[i]),
        list(map(lambda i: i * value, items)),
        list(filter(lambda v: v > value, items)),
    ]
result = f([3, 1, 2], {1: 30, 2: 10, 3: 20}, 1)
""",
    """
def get_sorted_ids(id_list, data):
    return sorted(id_list, key=lambda i: data[i]["order"])
result = get_sorted_ids(["a", "b", "c"], {"a": {"order": 3}, "b": {"order": 1}, "c": {"order": 2}})
""",
    """
def f():
    x = 1
    g = lambda: x
    del x
    try:
        g()
    except NameError as exc:
        return f"{type(exc).__name__}: {exc}"
result = f()
""",
    """
def f():
    g = lambda: x
    try:
        g()
    except NameError as exc:
        r = f"{type(exc).__name__}: {exc}"
    x = 3
    return [r, g()]
result = f()
""",
    "G = 1\ndef f():\n    return lambda: G\ng = f()\nG = 2\nresult = g()",
    """
G = 1
def f():
    g = lambda: G
    a = g()
    global G
    G = 2
    return [a, g()]
result = f()
""",
    """
def f():
    n = 0
    g = lambda: n
    def bump():
        nonlocal n
        n += 5
        return [g(), (lambda: n)()]
    return [bump(), n, g()]
result = f()
""",
    """
def f():
    i = 10
    s = sorted([i for i in range(3)], key=lambda v: -v) + [i * 2 for i in range(2)] + list({i for i in range(2)})
    def g():
        pass
    t = list({i: i for i in range(2)})
    return [s, t, i, (lambda: i)()]
result = f()
""",
    "def f():\n    return [(lambda: i)() for i in range(3)]\nresult = f()",
    "def f(rows, cols):\n    return [sorted(rows, key=lambda r: r[c]) for c in cols]\nresult = f([(1, 'b'), (2, 'a')], [0, 1])",
    "def f():\n    x = [1, 2]\n    return [x for x in map(lambda v: v + len(x), x)]\nresult = f()",
    "def f():\n    return [sum((lambda: j)() for j in range(4)), [h() for h in (lambda: j for j in range(3))]]\nresult = f()",
    "def f(ts, data):\n    return list(list(filter(lambda v: v > t, data)) for t in ts)\nresult = f([0, 1], [0, 1, 2])",
    "def f():\n    k = 3\n    return [(lambda: (y := 4))(), 'y' in locals(), (lambda: sum(x * k for x in range(3)))()]\nresult = f()",
    """
class Scale:
    def __init__(self, n):
        self.n = n
    def apply(self, vals):
        return list(map(lambda v: v * self.n, vals))
result = Scale(3).apply([1, 2])
""",
    "def f():\n    import math\n    return (lambda v: math.sqrt(v))(16)\nresult = f()",
    """
def f():
    try:
        raise ValueError('boom')
    except ValueError as exc:
        return (lambda: str(exc))()
result = f()
""",
    "z = 1\nresult = [(lambda: z)(), (lambda v=z: v)(), (lambda: z).__name__, type(lambda: z).__name__]",
    "x = 'global'\ndef f():\n    x = 1\n    return eval('(lambda: x)()')\nresult = f()",
    "i = 'global'\ndef f():\n    return [g() for g in [lambda: i for i in range(3)]]\nresult = f()",
    "i = 'global'\ndef f():\n    return [g() for g in {i: lambda: i for i in range(3)}.values()]\nresult = f()",
    "i = 'global'\ndef f():\n    return sorted(g() for g in {lambda: i for i in range(3)})\nresult = f()",
    "i = 'global'\ndef f():\n    return [g() for g in list(lambda: i for i in range(3))]\nresult = f()",
    """
def f():
    i = 'own'
    fs = [lambda: (i, j) for i in range(2) for j in 'ab']
    gs = list(lambda: i for i in range(3))
    def inner():
        return i
    return fs, gs, i, inner(), (lambda: i)()
fs, gs, own, inner, own_lambda = f()
result = [[h() for h in fs], [h() for h in gs], own, inner, own_lambda]
""",
    """
def f():
    g = (lambda: i for i in range(3))
    first = next(g)
    a = first()
    second = next(g)
    return [a, first(), second()]
result = f()
""",
    "result = [(lambda: lambda: 0)().__qualname__, (lambda: [x for x in (lambda: 1,)])()[0].__qualname__]",
    """
def f():
    try:
        raise ValueError("boom")
    except ValueError as exc:
        g = lambda: str(exc)
    try:
        g()
    except NameError as err:
        result = [type(err).__name__]
    exc = "later"
    return result + [g()]
result = f()
""",
    """
def f():
    try:
        raise ValueError("boom")
    except ValueError as exc:
        def g():
            return str(exc)
    exc = "later"
    return g()
result = f()
""",
    """
def f():
    try:
        raise ValueError("boom")
    except ValueError as exc:
        del exc
    return "ok"
result = f()
""",
    "x = 99\ndef f():\n    x: int = 3\n    return (lambda: x)()\nresult = f()",
    """
x = 99
def f(x):
    def g():
        return [(lambda: ([x for x in range(3)], x))(), (lambda: [v * 2 for v in [x for x in x]])()]
    return g()
result = f([1, 2])
""",
    """
x = 99
def f(x):
    def g():
        a = lambda: (x := 2)
        b = lambda: [x for x in range(3)]
        return [eval("x"), "x" in locals(), a(), b()]
    return g()
result = f(5)
""",
    """
def f():
    return [(lambda: 0).__defaults__, (lambda a=1: 0).__defaults__, (lambda *, k=1: 0).__defaults__]
result = f() + [(lambda: 0).__defaults__, (lambda: 0).__kwdefaults__]
""",
    # super() with lambdas in methods; a lambda's native code can only call native methods
    """
class B(dict):
    def f(self):
        return "B" + str(len(self))
class C(B):
    def a(self):
        return (lambda: super(C, self).get("k"))()
    def b(self):
        return sorted(["x", "k"], key=lambda key: super(C, self).get(key, 0))
    def c(self):
        return [(lambda: super(C, self).get(key, -1))() for key in ["k", "z"]]
    def d(self):
        return list(map(lambda self: super(C, self).get("k"), [self]))
    def e(self, other):
        get = lambda: super(C, other).get("k")
        return get()
    def g(self):
        return [(lambda v=super().f(): v)(), (lambda v=super(C, self).f(): v)()]
    def h(self):
        def inner(me):
            return [super().f(), (lambda: super(C, me).get("k"))()]
        return inner(self)
    def i(self):
        return (lambda: super().f())()
c = C(k=5)
result = []
for name, args in [["a", []], ["b", []], ["c", []], ["d", []], ["e", [C(k=7)]], ["g", []], ["h", []], ["i", []]]:
    try:
        result.append([name, getattr(c, name)(*args)])
    except RuntimeError as exc:
        result.append([name, str(exc)])
""",
    # super() in comprehensions and generator expressions in a method that shares its variables with a lambda
    """
class B:
    def f(self):
        return "B" + str(self.n)
class C(B):
    def __init__(self, n):
        self.n = n
    def a(self):
        g = lambda: self
        return [super().f() for _ in range(2)] + [super(C, self).f() for _ in range(1)]
    def b(self):
        g = lambda: self
        return list(super(C, self).f() for _ in range(2)) + [super().f()]
    def c(self):
        g = lambda: self
        return list(super().f() for _ in range(1))
    def d(self):
        g = lambda: self
        return [{k: super().f() for k in range(2)}, [[super().f() for _ in range(1)] for _ in range(2)]]
    def e(self):
        self = C(2)
        g = lambda: self
        return super().f()
    def h(self):
        g = lambda: self
        del self
        return super()
    def i(self):
        def inner():
            return super().f()
        g = lambda: inner
        return inner()
c = C(1)
result = []
for name in "abcdehi":
    try:
        result.append([name, getattr(c, name)()])
    except (TypeError, RuntimeError) as exc:
        result.append([name, str(exc)])
""",
    # lambdas returned from a property getter, setter or deleter and from a classmethod, which see later changes
    """
class A:
    n = 2
    def __init__(self):
        self._x = 3
    @property
    def x(self):
        k = 10
        f = lambda v: v * k + self._x
        k = 20
        return f
    @x.setter
    def x(self, v):
        scale = v
        self.saved = lambda: (scale * self._x, (lambda: scale + v)())
        scale = v + 1
    @x.deleter
    def x(self):
        me = self
        self.on_del = lambda: me
        me = "changed"
    @classmethod
    def make(cls, k):
        g = lambda: (cls.n + k, cls.__name__, (lambda c=cls: c.n)())
        k += 100
        return g
class B(A):
    n = 50
a = A()
f = a.x
r = [f(1)]
a._x = 5
r.append(f(1))
a.x = 4
r.append(a.saved())
a._x = 6
r.append(a.saved())
del a.x
r.append(a.on_del())
g = A.make(1)
A.n = 7
result = r + [g(), B.make(2)(), A().make(3)(), B().make(4)()]
""",
    # lambdas made in comprehensions and generator expressions in a property and a classmethod, reusing loop names
    """
class A:
    def __init__(self):
        self._items = [1, 2, 3]
        self._v = 4
    @property
    def x(self):
        return self._v
    @property
    def fns(self):
        i = "own"
        fs = [lambda: i * 10 for i in self._items]
        gs = list((lambda i=i: i) for i in range(2))
        hs = [[lambda: (i, j) for j in range(2)] for i in range(2)]
        ks = [[lambda: i for i in range(i, i + 2)] for i in range(2)]
        d = {i: (lambda: i + self._v) for i in range(2)}
        return [[f() for f in fs], [g() for g in gs], [[h() for h in row] for row in hs], [[k() for k in row] for row in ks], {k: v() for k, v in d.items()}, i]
    @fns.setter
    def fns(self, v):
        x = 0
        self.got = [lambda: x for x in range(v)]
        self.gen = (lambda: x for x in range(v))
        self.defaults = [(lambda v=self.x: (v, x))() for x in range(2)]
        self.after = x
    @classmethod
    def build(cls, n):
        named = [lambda: (cls, n) for cls in ["p", "q"]]
        gen = list(lambda: cls for cls in range(2))
        return [[f() for f in named], [f() for f in gen], cls.__name__, [(lambda: x + n)() for x in range(n)], sum(x for x in (lambda: range(n))())]
a = A()
r = a.fns
a.fns = 3
result = [r, [f() for f in a.got], [f() for f in a.gen], a.defaults, a.after, A.build(2)]
""",
    # a lambda used as a property getter or setter, using the variables of the function it's made in
    """
class A:
    def __init__(self, v):
        self.v = v
    @classmethod
    def add_scaled(cls, name, k):
        setattr(cls, name, property(lambda self: (self.v * k, cls.__name__)))
        k += 1
def add_props(cls, k):
    base = 10
    cls.x = property(lambda self: self.v + k)
    cls.y = property(lambda self: [self.v + base + i for i in range(2)], lambda self, val: setattr(self, "v", val - base))
    base = 20
    k += 1
    return lambda: (k, base)
def make_prop(scale):
    return property(lambda self: self.v * scale)
class B(A):
    z = make_prop(3)
    @property
    def w(self):
        return [self.v, self.x, self.z]
seen = add_props(A, 10)
A.add_scaled("s", 2)
b = B(1)
r = [b.x, b.y, b.z, b.w, b.s, seen(), type(A.x).__name__]
b.y = 25
result = r + [b.v, b.x, b.y, b.z, b.w, b.s]
""",
]


#
# comprehensions and generator expressions in functions, with and without lambdas or inner functions
#
COMPREHENSION_SCOPE_TESTS = [
    "def f():\n    x = 7\n    return [x for x in (x for _ in range(2))]\nresult = f()",
    "def f():\n    x = 7\n    g = (x for _ in range(2))\n    return [[x for x in g], x]\nresult = f()",
    "def f():\n    x = 7\n    g = (x for _ in range(2))\n    return [x for x in [1] for y in g]\nresult = f()",
    """
def f():
    out = []
    for i in range(3):
        g = (i * 10 for _ in range(1))
        out.append([i for i in g])
    return out
result = f()
""",
    """
def f():
    saved = []
    return list((saved.append(y + 100 for _ in range(1)) or [list(s) for s in saved[:-1]]) for y in range(3))
result = f()
""",
    """
def f():
    y = "own"
    saved = []
    r = [(saved.append(y for _ in range(1)) or [list(s) for s in saved[:-1]]) for y in range(3)]
    return r, y
result = f()
""",
    """
def f():
    x = 3
    try:
        [x for x in (1 / (x - 3) for _ in range(1))]
    except ZeroDivisionError:
        pass
    return [x, [x for x in (x for _ in range(1))]]
result = f()
""",
    "def f(vals):\n    return sorted({k: [v for v in vals if v % k == 0] for k in (2, 3)}.items())\nresult = f(range(10))",
    "def f():\n    x = 0\n    g = ((x := x + 1) for _ in range(3))\n    return [x for x in g]\nresult = f()",
    "def f():\n    x = 0\n    g = ((x := x + 1) for _ in range(3))\n    return {x: x for x in g}\nresult = f()",
    "def f():\n    x = 0\n    g = ((x := x + 1) for _ in range(3))\n    return sorted({x for x in g})\nresult = f()",
]


#
# the same, but only in a function that shares its variables with an inner function or lambda;
# otherwise a comprehension reading a generator expression that uses its loop variable's name
# sees the comprehension's loop variable, as it did before
#
SHARED_COMPREHENSION_SCOPE_TESTS = [
    "def f():\n    x = 7\n    return [{x: x for x in (x for _ in range(2))}, {x for x in (x + 1 for _ in range(2))}]\nresult = f()",
    "def f():\n    x = 7\n    return [[x for x in (x + 1 for _ in range(3))], list(x for x in (x + 1 for _ in range(3)))]\nresult = f()",
    """
def f():
    x = 1
    g = (x * 10 for _ in range(3))
    first = next(g)
    x = 2
    return [first, [x for x in g], x]
result = f()
""",
    """
def f():
    x = 5
    a = (x for _ in range(2))
    b = (x + y for y in a)
    return [[x for x in b], sum(x for x in (x for _ in range(3)))]
result = f()
""",
    "def f():\n    x = 0\n    g = ((x := x + 1) for _ in range(3))\n    return [[x for x in g], x]\nresult = f()",
    """
def f():
    x = 0
    g = ((x := x + 1) for _ in range(3))
    d = {x: x for x in g}
    h = ((x := x + 1) for _ in range(2))
    return [d, sorted({x for x in h}), x]
result = f()
""",
    "def f():\n    x = 0\n    g = ((x := x + 1) for _ in range(3))\n    return [eval('[x for x in g]'), x]\nresult = f()",
]


async def run_tests_compared_with_python(hass, sources):
    """Run each script in CPython and in pyscript, and check they give the same result."""
    hass.data[DOMAIN] = {CONFIG_ENTRY: MockConfigEntry(domain=DOMAIN, data={CONF_ALLOW_ALL_IMPORTS: True})}
    DecoratorRegistry.init(hass)
    Function.init(hass)
    State.init(hass)
    State.register_functions()
    TrigTime.init(hass)

    for source in sources:
        expected_ns = {}
        exec(compile(source, "test", "exec"), expected_ns)  # pylint: disable=exec-used
        global_ctx = GlobalContext("test", global_sym_table={}, manager=GlobalContextMgr)
        ast = AstEval("test", global_ctx=global_ctx)
        Function.install_ast_funcs(ast)
        ast.parse(source)
        await ast.eval()
        assert global_ctx.global_sym_table["result"] == expected_ns["result"], source

    await Function.waiter_sync()
    await Function.waiter_stop()
    await Function.reaper_stop()


async def test_lambdas_match_python(hass):
    """Test that a lambda reads the variables of the functions it's in, compared with CPython."""
    await run_tests_compared_with_python(hass, LAMBDA_TESTS)


def with_inner_function(source):
    """Return the script with an inner function in f, so f shares its variables."""
    lines = source.split("\n")
    def_line = next(i for i, line in enumerate(lines) if line.startswith("def f("))
    lines.insert(def_line + 1, "    def _inner():\n        pass")
    return "\n".join(lines)


async def test_comprehension_scopes_match_python(hass):
    """Test comprehension and generator expression loop variables in functions, compared with CPython."""
    sources = COMPREHENSION_SCOPE_TESTS + [
        with_inner_function(source)
        for source in COMPREHENSION_SCOPE_TESTS + SHARED_COMPREHENSION_SCOPE_TESTS
    ]
    await run_tests_compared_with_python(hass, sources)


#
# a := in a generator expression's element, run by a later comprehension whose loop variable has
# the same name, in a function that shares its variables with an inner function or lambda
#
GENEXP_WALRUS_SCOPE_TESTS = [
    # the name has no value yet, or is deleted and assigned again
    "def f():\n    g = ((x := i) for i in range(3))\n    r = [x for x in g]\n    return r, x\nresult = f()",
    """
def f():
    x = 0
    del x
    g = ((x := i * 2) for i in range(3))
    r = [x for x in g]
    return r, x
result = f()
""",
    """
def f():
    x = 0
    g = ((x := x + 1) for _ in range(2))
    r1 = [x for x in g]
    del x
    h = ((x := 10 + i) for i in range(2))
    r2 = [x for x in h]
    return r1, r2, x
result = f()
""",
    # a lambda reading the name, a lambda made in the element, and a := in a lambda's default
    """
def f():
    x = 0
    read = lambda: x
    g = ((x := x + 1) for _ in range(3))
    r = [(x, read()) for x in g]
    return r, x, read()
result = f()
""",
    """
def f():
    x = 0
    g = ((x := x + 1, lambda: x)[1] for _ in range(3))
    fs = [x for x in g]
    return [h() for h in fs], x
result = f()
""",
    """
def f():
    x = 0
    g = ((lambda v=(x := x + 1): v)() for _ in range(3))
    r = [x for x in g]
    return r, x
result = f()
""",
    # nested comprehensions and generator expressions that reuse the name
    """
def f():
    x = 0
    g = ((x := x + 1) + sum([x for x in range(2)]) for _ in range(3))
    h = ((x := x + 1) * 10 + sum(x for x in range(2)) for _ in range(3))
    return [x for x in g], [x for x in h], x
result = f()
""",
    """
def f():
    x = 0
    g = ([(x := x + 1) for _ in range(2)] for _ in range(2))
    h = (list((x := x + 1) for _ in range(2)) for _ in range(2))
    return [x for x in g], [x for x in h], x
result = f()
""",
    """
def f():
    x = 0
    g = ((x := x + 1) for _ in range(3))
    r = [[x for x in g] for x in range(1)]
    return r, x
result = f()
""",
    # several elements: two loops, two generator expressions, and next() in a comprehension
    """
def f():
    x = 0
    g = ((x := x + i) for i in range(3) for j in range(2))
    r = [x for x in g]
    return r, x
result = f()
""",
    """
def f():
    x = 0
    a = ((x := x + 1) for _ in range(3))
    b = ((x := x * 10) for _ in range(3))
    r = [x for x in zip(a, b)]
    return r, x
result = f()
""",
    """
def f():
    x = 0
    g = ((x := x + 1) for _ in range(3))
    r = [next(g) + x for x in range(10, 12)]
    return r, x
result = f()
""",
    # an element that raises, after or before its :=
    """
def f():
    x = 0
    g = ((x := x + 1) / (2 - x) for _ in range(5))
    try:
        r = [x for x in g]
    except ZeroDivisionError:
        r = "raised"
    h = ((1 / (4 - x)) + (x := x + 1) for _ in range(5))
    try:
        s = [x for x in h]
    except ZeroDivisionError:
        s = "raised"
    return r, s, x
result = f()
""",
    # an inner function that assigns or deletes the name with nonlocal, and a global name
    """
def f():
    x = 0
    def bump():
        nonlocal x
        x += 1
        return x
    r = [x for x in (bump() for _ in range(3))]
    def drop():
        nonlocal x
        del x
        return 0
    s = [x for x in (drop() for _ in range(1))]
    try:
        return r, s, x
    except NameError:
        return r, s, "unbound"
result = f()
""",
    """
x = 100
def f():
    global x
    g = ((x := x + 1) for _ in range(3))
    r = [x for x in g]
    return r, x
result = f()
""",
]


async def test_genexp_walrus_scopes_match_python(hass):
    """Test := in a generator expression run by a comprehension reusing the name, compared with CPython."""
    await run_tests_compared_with_python(
        hass, [with_inner_function(source) for source in GENEXP_WALRUS_SCOPE_TESTS]
    )


# scripts compared with CPython; the first 44 came with the first version of this change
STOP_ITERATION_TESTS = [
    """
it = iter([1])
result = [next(it), next(it, "default")]
try:
    next(it)
except StopIteration as exc:
    result.append([type(exc).__name__, exc.args, exc.value])
""",
    """
def func():
    it = iter([])
    try:
        next(it)
    except StopIteration:
        return "caught"
    return "not caught"
result = func()
""",
    """
result = []
for exc_raised in [StopIteration, StopIteration(5), StopIteration(5, 6)]:
    try:
        raise exc_raised
    except StopIteration as exc:
        result.append([type(exc).__name__, exc.args, exc.value])
""",
    """
result = []
for value in [[], "x"]:
    try:
        int(next(iter(value)))
    except (StopIteration, ValueError) as exc:
        result.append(type(exc).__name__)
    try:
        int(next(iter(value)))
    except (ValueError, StopIteration) as exc:
        result.append(type(exc).__name__)
""",
    """
try:
    raise StopIteration(5)
except Exception as exc:
    result = [type(exc).__name__, isinstance(exc, StopIteration), exc.value, exc.__cause__, exc.__context__]
""",
    """
result = []
try:
    try:
        next(iter([]))
    except RuntimeError:
        result.append("RuntimeError")
    except ValueError:
        result.append("ValueError")
except StopIteration as exc:
    result.append(type(exc).__name__)
""",
    """
result = []
try:
    try:
        raise StopIteration("inner")
    except StopIteration as exc:
        result.append(exc.args)
        raise
except StopIteration as exc:
    result.append(exc.args)
""",
    """
result = []
try:
    try:
        next(iter([]))
    except StopIteration as exc:
        inner = exc
        raise exc
except StopIteration as exc:
    result.append(exc is inner)
""",
    """
import contextlib
class Ctx:
    def __init__(self, suppress):
        self.suppress = suppress
    def __enter__(self):
        return self
    def __exit__(self, exc_type, exc, tb):
        result.append([exc_type.__name__, type(exc).__name__, exc.args, tb is exc.__traceback__])
        return self.suppress
result = []
with Ctx(True):
    next(iter([]))
result.append("suppressed")
try:
    with Ctx(False):
        raise StopIteration(3)
except StopIteration as exc:
    result.append(["not suppressed", exc.value])
with contextlib.suppress(StopIteration):
    next(iter([]))
result.append("contextlib.suppress")
@contextlib.contextmanager
@pyscript_compile
def manager(suppress):
    try:
        yield
    except StopIteration as exc:
        result.append(["generator manager", exc.args])
        if not suppress:
            raise
with manager(True):
    raise StopIteration("x")
try:
    with manager(False):
        raise StopIteration("y")
except StopIteration as exc:
    result.append(["not suppressed", exc.args])
try:
    with contextlib.nullcontext():
        next(iter([]))
except StopIteration:
    result.append("nullcontext")
""",
    """
class AsyncCtx:
    async def __aenter__(self):
        return self
    async def __aexit__(self, exc_type, exc, tb):
        result.append(["aexit", exc_type.__name__, exc.value])
        return True
result = []
async with AsyncCtx():
    raise StopIteration(4)
result.append("after")
""",
    """
def inner(value):
    raise StopIteration(value)
def middle(value):
    return inner(value) + 1
def outer():
    try:
        middle(7)
    except StopIteration as exc:
        return ["caught", exc.value]
result = outer()
try:
    middle(8)
except StopIteration as exc:
    result.append(exc.value)
""",
    """
class Countdown:
    def __init__(self, n):
        self.n = n
    @pyscript_compile
    def __iter__(self):
        return self
    @pyscript_compile
    def __next__(self):
        if self.n <= 0:
            raise StopIteration
        self.n -= 1
        return self.n
result = [x for x in Countdown(3)]
for y in Countdown(2):
    result.append(y)
it = Countdown(1)
result.append(next(it))
try:
    next(it)
except StopIteration:
    result.append("caught")
""",
    """
class Countdown:
    def __init__(self, n):
        self.n = n
    def next_value(self):
        if self.n <= 0:
            raise StopIteration(f"done at {self.n}")
        self.n -= 1
        return self.n
it = Countdown(2)
result = []
while True:
    try:
        result.append(it.next_value())
    except StopIteration as exc:
        result.append(exc.value)
        break
""",
    """
result = []
for x in iter([1, 2]):
    result.append(x)
result.append(list(x for x in iter([3, 4])))
result.append(sum(x for x in []))
g = (x for x in [])
try:
    next(g)
except StopIteration as exc:
    result.append(["exhausted", exc.args])
""",
    """
result = []
it = iter([])
try:
    list(next(it) for x in range(3))
except StopIteration:
    result.append("StopIteration")
except RuntimeError as exc:
    result.append([str(exc), type(exc.__cause__).__name__])
try:
    list(x for x in range(3) if next(it))
except RuntimeError as exc:
    result.append([str(exc), type(exc.__cause__).__name__])
def raises(value):
    raise StopIteration(value)
try:
    sum(raises(x) for x in range(3))
except RuntimeError as exc:
    result.append([str(exc), exc.__cause__.value])
try:
    next(raises(x) for x in range(3))
except RuntimeError as exc:
    result.append([str(exc), exc.__cause__.value])
""",
    """
result = []
try:
    try:
        next(iter([]))
    except StopIteration as exc:
        exc.__traceback__ = None
        raise
except Exception as exc:
    result.append(type(exc).__name__)
""",
    """
result = []
try:
    try:
        next(iter([]))
    finally:
        import sys
        sys.exception().__traceback__ = None
except Exception as exc:
    result.append(type(exc).__name__)
""",
    """
class Payload:
    @pyscript_compile
    def __eq__(self, other):
        raise ValueError('unexpected comparison')
result = []
try:
    try:
        raise RuntimeError(Payload())
    except RuntimeError:
        result.append('caught RuntimeError')
except ValueError as exc:
    result.append(str(exc))
""",
    """
result = []
class Payload:
    @pyscript_compile
    def __eq__(self, other):
        result.append('comparison')
        return False
try:
    raise RuntimeError(Payload())
except RuntimeError:
    result.append('caught')
""",
    """
import functools
@pyscript_compile
def raises(value=None):
    raise StopIteration(4)
class Native:
    @property
    @pyscript_compile
    def value(self):
        raise StopIteration(4)
result = []
for mode in range(4):
    try:
        if mode == 0:
            raises()
        elif mode == 1:
            functools.partial(raises)()
        elif mode == 2:
            sorted([2, 1], key=raises)
        else:
            Native().value
    except Exception as exc:
        result.append([type(exc).__name__, exc.args])
""",
    """
@pyscript_compile
def raises(x):
    raise StopIteration(9)
result = list(map(raises, [1, 2]))
""",
    """
@pyscript_compile
def reraise():
    raise
result = []
try:
    try:
        next(iter([]))
    except StopIteration:
        reraise()
except Exception as exc:
    result.append(type(exc).__name__)
""",
    """
result = []
saved = None
try:
    next(iter([]))
except StopIteration as exc:
    saved = exc
for x in range(3):
    try:
        raise saved
    except StopIteration as exc:
        result.append([exc is saved, exc.__context__, exc.__cause__])
""",
    """
result = []
try:
    for x in [1, 2]:
        next(iter([]))
except StopIteration:
    result.append('caught')
""",
    """
result = []
for mode in range(3):
    try:
        if mode == 0:
            [next(iter([])) for x in [1]]
        elif mode == 1:
            {next(iter([])) for x in [1]}
        else:
            {x: next(iter([])) for x in [1]}
    except Exception as exc:
        result.append(type(exc).__name__)
""",
    """
result = []
try:
    list(next(iter([])) for x in [1])
except RuntimeError as exc:
    result.append([type(exc.__context__).__name__, exc.__context__ is exc.__cause__, exc.__cause__.__context__])
""",
    """
class Native:
    @pyscript_compile
    def __setitem__(self, key, value):
        raise StopIteration(key)
    @pyscript_compile
    def __iter__(self):
        raise StopIteration("iter")
    @pyscript_compile
    def __hash__(self):
        raise StopIteration("hash")
    def get_value(self):
        return 0
    @pyscript_compile
    def set_value(self, value):
        raise StopIteration(value)
    value = property(get_value, set_value)
def args(*values):
    return values
result = []
for mode in range(7):
    try:
        if mode == 0:
            Native()["key"] = 1
        elif mode == 1:
            Native().value = 2
        elif mode == 2:
            [*Native()]
        elif mode == 3:
            args(*Native())
        elif mode == 4:
            [x for x in Native()]
        elif mode == 5:
            {Native() for x in [1]}
        else:
            {Native(): x for x in [1]}
    except Exception as exc:
        result.append([type(exc).__name__, exc.args])
""",
    """
class Native:
    @pyscript_compile
    def __add__(self, other):
        raise StopIteration("add")
try:
    Native() + 1
except StopIteration as exc:
    result = ["caught", exc.args]
except RuntimeError:
    result = "wrong exception"
""",
    """
class Native:
    @pyscript_compile
    def __eq__(self, other):
        raise StopIteration("eq")
try:
    Native() == 1
except StopIteration as exc:
    result = ["caught", exc.args]
except RuntimeError:
    result = "wrong exception"
""",
    """
class Native:
    @pyscript_compile
    def __bool__(self):
        raise StopIteration("bool")
try:
    not Native()
except StopIteration as exc:
    result = ["caught", exc.args]
except RuntimeError:
    result = "wrong exception"
""",
    """
class Native:
    @pyscript_compile
    def __neg__(self):
        raise StopIteration("neg")
try:
    -Native()
except StopIteration as exc:
    result = ["caught", exc.args]
except RuntimeError:
    result = "wrong exception"
""",
    """
class Native:
    @pyscript_compile
    def __contains__(self, item):
        raise StopIteration("contains")
try:
    1 in Native()
except StopIteration as exc:
    result = ["caught", exc.args]
except RuntimeError:
    result = "wrong exception"
""",
    """
class Namespace(dict):
    @pyscript_compile
    def __getitem__(self, key):
        if key == "boom":
            raise StopIteration("read " + key)
        return dict.__getitem__(self, key)
    @pyscript_compile
    def __setitem__(self, key, value):
        if key == "bang":
            raise StopIteration("write " + key)
        dict.__setitem__(self, key, value)
class Meta(type):
    @classmethod
    @pyscript_compile
    def __prepare__(mcs, name, bases):
        return Namespace()
result = []
class C(metaclass=Meta):
    boom = 1
    try:
        boom.real
    except StopIteration as exc:
        result.append(["read", exc.args])
    try:
        bang = 2
    except StopIteration as exc:
        result.append(["write", exc.args])
""",
    """
result = []
try:
    eval("next(iter([]))")
except StopIteration:
    result.append("eval")
try:
    exec("raise StopIteration(3)")
except StopIteration as exc:
    result.append(["exec", exc.value])
def func():
    try:
        exec("next(iter([]))")
    except StopIteration:
        return "in function"
result.append(func())
""",
    """
class Native:
    @pyscript_compile
    def __iter__(self):
        raise StopIteration("iter")
result = []
try:
    list(x for y in [1] for x in Native())
except RuntimeError as exc:
    result.append([str(exc), exc.__cause__.args])
try:
    for x in Native():
        pass
except StopIteration as exc:
    result.append(["for", exc.args])
""",
    """
@pyscript_compile
def deco(func):
    raise StopIteration("deco")
result = []
try:
    @deco
    @pyscript_compile
    async def compiled_async():
        pass
except StopIteration as exc:
    result.append(["async def", exc.args])
try:
    @deco
    @pyscript_compile
    def compiled():
        pass
except StopIteration as exc:
    result.append(["def", exc.args])
try:
    @deco
    def interpreted():
        pass
except StopIteration as exc:
    result.append(["interpreted def", exc.args])
""",
    """
class Thing:
    def __init__(self, items):
        self.first = next(iter(items))
class Raises:
    def __init__(self):
        raise StopIteration(7)
result = []
for cls, args in [(Thing, [[]]), (Raises, [])]:
    try:
        cls(*args)
    except StopIteration as exc:
        result.append([cls.__name__, exc.args, exc.value])
result.append(Thing([4]).first)
""",
    """
class Base:
    def __init__(self, value):
        if value is None:
            raise StopIteration("base")
        self.value = value
class Child(Base):
    def __init__(self, value):
        super().__init__(value)
class Other(Base):
    def __init__(self, value):
        Base.__init__(self, value)
class NativeBase:
    @pyscript_compile
    def __init__(self, value):
        raise StopIteration("native base")
class NativeChild(NativeBase):
    def __init__(self, value):
        super().__init__(value)
result = []
for cls in [Child, Other, NativeChild]:
    try:
        cls(None)
    except StopIteration as exc:
        result.append([cls.__name__, exc.args])
result.append(Child(3).value)
""",
    """
class Made:
    def __new__(cls, ok):
        if not ok:
            raise StopIteration("new")
        return object.__new__(cls)
class NativeNew:
    @pyscript_compile
    def __new__(cls, ok):
        raise StopIteration("native new")
    def __init__(self, ok):
        self.ok = ok
result = []
for cls in [Made, NativeNew]:
    try:
        cls(False)
    except StopIteration as exc:
        result.append([cls.__name__, exc.args])
result.append(type(Made(True)).__name__)
""",
    """
class MyStop(StopIteration):
    pass
class Mixed:
    @property
    @pyscript_compile
    def native(self):
        raise MyStop("native get")
    @property
    def value(self):
        raise StopIteration("get")
    @value.setter
    def value(self, v):
        raise MyStop(v)
    @value.deleter
    def value(self):
        next(iter([]))
    @classmethod
    def make(cls, v):
        raise MyStop(cls.__name__, v)
obj = Mixed()
result = []
for mode in range(7):
    try:
        if mode == 0:
            obj.native
        elif mode == 1:
            obj.value
        elif mode == 2:
            obj.value = 3
        elif mode == 3:
            del obj.value
        elif mode == 4:
            Mixed.make(4)
        elif mode == 5:
            obj.make(5)
        else:
            obj.value += 1
    except StopIteration as exc:
        result.append([mode, type(exc).__name__, exc.value, exc.args])
""",
    """
class MyStop(StopIteration):
    pass
class Native:
    def __init__(self):
        self._x = 3
    @property
    def x(self):
        return self._x
    @x.setter
    @pyscript_compile
    def x(self, value):
        if value > 5:
            raise MyStop(value)
        raise StopIteration(value)
    @x.deleter
    @pyscript_compile
    def x(self):
        raise StopIteration("del")
obj = Native()
result = []
for mode in range(4):
    try:
        if mode == 0:
            obj.x = 2
        elif mode == 1:
            obj.x += 1
        elif mode == 2:
            obj.x = 9
        else:
            del obj.x
    except StopIteration as exc:
        result.append([mode, type(exc).__name__, exc.value, exc.args])
result.append(obj.x)
""",
    """
class MyStop(StopIteration):
    pass
class Guarded:
    @pyscript_compile
    def __setattr__(self, name, value):
        if name == "locked":
            raise MyStop(name, value)
        object.__setattr__(self, name, value)
    @pyscript_compile
    def __delattr__(self, name):
        raise StopIteration("del " + name)
obj = Guarded()
obj.free = 1
obj.free += 1
result = [obj.free]
for mode in range(2):
    try:
        if mode == 0:
            obj.locked = 2
        else:
            del obj.free
    except StopIteration as exc:
        result.append([mode, type(exc).__name__, exc.value, exc.args])
result.append(obj.free)
""",
    """
class MyStop(StopIteration):
    pass
class Meta(type):
    @pyscript_compile
    def __setattr__(cls, name, value):
        if name == "frozen":
            raise MyStop(value)
        type.__setattr__(cls, name, value)
    @pyscript_compile
    def __delattr__(cls, name):
        raise StopIteration("del " + name)
@pyscript_compile
def get_level(cls):
    return 1
@pyscript_compile
def set_level(cls, value):
    raise StopIteration(value)
@pyscript_compile
def del_level(cls):
    raise MyStop("del level")
class MetaProp(type):
    level = property(get_level, set_level, del_level)
class C(metaclass=Meta):
    pass
class D(metaclass=MetaProp):
    pass
result = []
for mode in range(5):
    try:
        if mode == 0:
            C.frozen = 1
        elif mode == 1:
            del C.anything
        elif mode == 2:
            D.level = 5
        elif mode == 3:
            D.level += 1
        else:
            del D.level
    except StopIteration as exc:
        result.append([mode, type(exc).__name__, exc.value, exc.args])
C.open = 2
result.append([C.open, D.level])
""",
    """
class MyStop(StopIteration):
    pass
class Fallback:
    @property
    def value(self):
        raise AttributeError("value")
    def __getattr__(self, name):
        raise MyStop("fallback " + name)
class NativeFallback:
    @property
    def value(self):
        raise AttributeError("value")
    @pyscript_compile
    def __getattr__(self, name):
        raise StopIteration("native fallback " + name)
result = []
for cls in [Fallback, NativeFallback]:
    try:
        cls().value
    except StopIteration as exc:
        result.append([cls.__name__, type(exc).__name__, exc.value, exc.args])
""",
    """
class MyStop(StopIteration):
    pass
result = []
try:
    raise StopIteration(5)
except BaseException as exc:
    result.append([type(exc).__name__, exc.value])
try:
    next(iter([]))
except:
    result.append("bare except")
try:
    raise MyStop(1)
except StopIteration as exc:
    result.append([type(exc).__name__, exc.value])
try:
    try:
        next(iter([]))
    except RuntimeError:
        result.append("RuntimeError")
except StopIteration:
    result.append("StopIteration")
""",
    """
result = []
for msg in ["coroutine raised StopIteration", "async generator raised StopIteration"]:
    for cause in [None, ValueError("v"), StopIteration("s")]:
        try:
            if msg.startswith("coroutine") and isinstance(cause, StopIteration):
                continue
            raise RuntimeError(msg) from cause
        except StopIteration:
            result.append("StopIteration")
        except RuntimeError as exc:
            result.append([str(exc), type(exc.__cause__).__name__])
""",
    """
@pyscript_compile
def gen():
    yield 1
    raise StopIteration("gen")
@pyscript_compile
async def agen():
    yield 1
    raise StopIteration("agen")
@pyscript_compile
async def drain():
    return [x async for x in agen()]
result = []
try:
    list(gen())
except RuntimeError as exc:
    result.append([str(exc), exc.__cause__.args])
try:
    await drain()
except StopIteration:
    result.append("StopIteration")
except RuntimeError as exc:
    result.append([str(exc), exc.__cause__.args])
""",
    """
class Truth:
    @pyscript_compile
    def __bool__(self):
        raise StopIteration("bool")
class Exits:
    def __init__(self, mode):
        self.mode = mode
    def __enter__(self):
        return self
    def __exit__(self, *args):
        if self.mode == "raise":
            raise StopIteration("exit")
        return Truth()
result = []
for mode in ["raise", "truth"]:
    for fail in [False, True]:
        try:
            with Exits(mode):
                if fail:
                    raise ValueError("body")
            result.append([mode, fail, "no error"])
        except StopIteration as exc:
            result.append([mode, fail, exc.args])
""",
    """
class CustomStop(StopIteration):
    @pyscript_compile
    def with_traceback(self, tb):
        raise ValueError("with_traceback called")
    @property
    @pyscript_compile
    def __traceback__(self):
        return "not the traceback"
def boom():
    raise CustomStop("needle")
class Ctx:
    def __enter__(self):
        return self
    def __exit__(self, exc_type, exc, tb):
        result.append([exc_type.__name__, exc.args, type(tb).__name__])
        return True
result = []
try:
    list(boom() for x in [1])
except RuntimeError as exc:
    result.append([str(exc), type(exc.__cause__).__name__, exc.__cause__.args, exc.__context__ is exc.__cause__])
with Ctx():
    boom()
""",
]


async def test_stop_iteration_matches_python(hass):
    """Test that StopIteration can be caught and handled like in Python, compared with CPython itself."""
    hass.data[DOMAIN] = {CONFIG_ENTRY: MockConfigEntry(domain=DOMAIN, data={CONF_ALLOW_ALL_IMPORTS: True})}
    Function.init(hass)
    State.init(hass)
    State.register_functions()
    TrigTime.init(hass)
    DecoratorRegistry.init(hass)

    for source in STOP_ITERATION_TESTS:
        expected_ns = {"pyscript_compile": lambda func: func}
        code = compile(source, "test", "exec", flags=ast_module.PyCF_ALLOW_TOP_LEVEL_AWAIT)
        # code compiled with top level await runs as a coroutine function, like the asyncio REPL
        if inspect.isawaitable(run := FunctionType(code, expected_ns)()):
            await run
        global_ctx = GlobalContext("test", global_sym_table={}, manager=GlobalContextMgr)
        ast = AstEval("test", global_ctx=global_ctx)
        Function.install_ast_funcs(ast)
        ast.parse(source)
        await ast.eval()
        assert global_ctx.global_sym_table["result"] == expected_ns["result"], source

    await Function.waiter_sync()
    await Function.waiter_stop()
    await Function.reaper_stop()


# documented limits: what pyscript does where it differs from Python
STOP_ITERATION_LIMITS = [
    # in a handler, an except clause's type and a finally block, the exception being handled is
    # Python's RuntimeError, which is also the context of a new exception; reraising still works
    [
        """
import sys
result = []
try:
    next(iter([]))
except StopIteration as exc:
    result.append([type(sys.exception()).__name__, sys.exc_info()[0].__name__, sys.exc_info()[1] is exc])
    try:
        int("x")
    except ValueError as exc2:
        result.append(type(exc2.__context__).__name__)
def handler():
    result.append(type(sys.exception()).__name__)
    return StopIteration
try:
    next(iter([]))
except handler():
    result.append("caught")
try:
    try:
        next(iter([]))
    except type(sys.exception()):
        result.append("inner")
except StopIteration:
    result.append("outer")
try:
    try:
        next(iter([]))
    except missing:
        pass
except NameError as exc:
    result.append(type(exc.__context__).__name__)
try:
    try:
        next(iter([]))
    finally:
        result.append(type(sys.exception()).__name__)
except StopIteration:
    result.append("caught")
try:
    try:
        next(iter([]))
    finally:
        int("x")
except ValueError as exc:
    result.append(type(exc.__context__).__name__)
try:
    try:
        next(iter([]))
    except StopIteration:
        raise StopIteration(6)
except StopIteration as exc:
    result.append([type(exc.__context__).__name__, exc.value])
try:
    try:
        next(iter([]))
    except StopIteration:
        raise
except StopIteration:
    result.append("raise")
try:
    try:
        next(iter([]))
    except StopIteration as exc:
        raise exc
except StopIteration:
    result.append("raise exc")
""",
        [
            ["RuntimeError", "RuntimeError", False],
            "RuntimeError",
            "RuntimeError",
            "caught",
            "outer",
            "RuntimeError",
            "RuntimeError",
            "caught",
            "RuntimeError",
            ["RuntimeError", 6],
            "raise",
            "raise exc",
        ],
    ],
    # where Python itself gives RuntimeError("coroutine raised StopIteration") because a
    # StopIteration left a native coroutine, a native task or a pyscript async def, pyscript
    # catches the StopIteration it replaced, as it does for a script's own RuntimeError like that
    [
        """
import asyncio
@pyscript_compile
async def native():
    raise StopIteration(1)
@pyscript_compile
async def in_task():
    return await asyncio.create_task(native())
@pyscript_compile
def run_native():
    native().send(None)
@pyscript_compile
async def reraise():
    raise
async def interpreted():
    raise StopIteration(4)
result = []
for mode in range(6):
    try:
        if mode == 0:
            await native()
        elif mode == 1:
            await in_task()
        elif mode == 2:
            run_native()
        elif mode == 3:
            await interpreted()
        elif mode == 4:
            try:
                next(iter([]))
            except StopIteration:
                await reraise()
        else:
            try:
                next(iter([]))
            except StopIteration as exc:
                raise RuntimeError("coroutine raised StopIteration") from exc
    except Exception as exc:
        result.append([mode, type(exc).__name__, exc.args])
""",
        [
            [0, "StopIteration", (1,)],
            [1, "StopIteration", (1,)],
            [2, "StopIteration", (1,)],
            [3, "StopIteration", (4,)],
            [4, "StopIteration", ()],
            [5, "StopIteration", ()],
        ],
    ],
    # native code that awaits a pyscript function gets Python's RuntimeError
    [
        """
def raises(value):
    raise StopIteration(value)
@pyscript_compile
async def native(func):
    try:
        await func(2)
    except RuntimeError as exc:
        return [str(exc), exc.__cause__.value]
result = await native(raises)
""",
        ["coroutine raised StopIteration", 2],
    ],
]


async def test_stop_iteration_limits(hass):
    """Test what pyscript does where its StopIteration handling differs from Python."""
    hass.data[DOMAIN] = {CONFIG_ENTRY: MockConfigEntry(domain=DOMAIN, data={CONF_ALLOW_ALL_IMPORTS: True})}
    Function.init(hass)
    State.init(hass)
    State.register_functions()
    TrigTime.init(hass)
    DecoratorRegistry.init(hass)

    for source, expected in STOP_ITERATION_LIMITS:
        global_ctx = GlobalContext("test", global_sym_table={}, manager=GlobalContextMgr)
        ast = AstEval("test", global_ctx=global_ctx)
        Function.install_ast_funcs(ast)
        ast.parse(source)
        await ast.eval()
        assert global_ctx.global_sym_table["result"] == expected, source

    await Function.waiter_sync()
    await Function.waiter_stop()
    await Function.reaper_stop()


STOP_ITERATION_UNCAUGHT_TESTS = [
    ["x = 1\nnext(iter([]))\n", 'File "test", line 2, in None\n    next(iter([]))\n', "StopIteration\n"],
    [
        "def func(value):\n    raise StopIteration(value)\n\nfunc(5)\n",
        'File "test", line 2, in func\n    raise StopIteration(value)\n',
        "StopIteration: 5\n",
    ],
    [
        "try:\n    next(iter([]))\nexcept StopIteration:\n    raise\n",
        'File "test", line 2, in None\n    next(iter([]))\n',
        "StopIteration\n",
    ],
    # these make an exception chain that loops back to the StopIteration
    [
        "try:\n    next(iter([]))\nexcept StopIteration as exc:\n    raise exc\n",
        'File "test", line 4, in None\n    raise exc\n',
        "StopIteration\n",
    ],
    [
        "def func():\n    try:\n        raise StopIteration(3)\n    except StopIteration as exc:\n"
        "        try:\n            raise exc\n        except StopIteration as exc2:\n            raise exc2\n\nfunc()\n",
        'File "test", line 8, in func\n    raise exc2\n',
        "StopIteration: 3\n",
    ],
    # from a native function
    [
        "@pyscript_compile\ndef native(value):\n    raise StopIteration(value)\n\nnative(4)\n",
        'File "test", line 5, in None\n    native(4)\n',
        '  File "test", line 3, in native\nStopIteration: 4\n',
    ],
]


async def test_stop_iteration_uncaught_report(hass, caplog):
    """Test that an uncaught StopIteration is reported as itself, with the script line that raised it."""
    hass.data[DOMAIN] = {CONFIG_ENTRY: MockConfigEntry(domain=DOMAIN, data={CONF_ALLOW_ALL_IMPORTS: False})}
    Function.init(hass)
    State.init(hass)
    State.register_functions()
    TrigTime.init(hass)

    global_ctx = GlobalContext("test", global_sym_table={}, manager=GlobalContextMgr)
    ast = AstEval("test", global_ctx=global_ctx)
    Function.install_ast_funcs(ast)
    for source, frame, message in STOP_ITERATION_UNCAUGHT_TESTS:
        ast.parse(source)
        # Python only lets a StopIteration out of our async code inside a RuntimeError
        with pytest.raises(RuntimeError, match="coroutine raised StopIteration") as excinfo:
            await ast.eval()
        formatter = EvalExceptionFormatter(excinfo.value)
        formatted = "".join(formatter.format())
        assert isinstance(formatter.exc, StopIteration), source
        assert frame in formatted, formatted
        assert formatted.endswith(message), formatted
        assert "RuntimeError" not in formatted, formatted

    # an exception raised while handling it has the StopIteration as its context
    ast.parse("try:\n    next(iter([]))\nexcept StopIteration:\n    raise ValueError('bad')\n")
    with pytest.raises(ValueError, match="bad") as excinfo:
        await ast.eval()
    formatted = "".join(EvalExceptionFormatter(excinfo.value).format())
    assert formatted.startswith('  File "test", line 2, in None\n    next(iter([]))\n'), formatted
    assert "StopIteration\n\nDuring handling of the above exception" in formatted, formatted
    assert "RuntimeError" not in formatted, formatted

    # or as its cause, with the line that raised it
    ast.parse(
        "try:\n    raise StopIteration(1)\nexcept StopIteration as exc:\n    raise ValueError('bad') from exc\n"
    )
    with pytest.raises(ValueError, match="bad") as excinfo:
        await ast.eval()
    formatted = "".join(EvalExceptionFormatter(excinfo.value).format())
    assert formatted.startswith(
        '  File "test", line 2, in None\n    raise StopIteration(1)\nStopIteration: 1\n\n'
    )
    assert "The above exception was the direct cause" in formatted, formatted
    assert "RuntimeError" not in formatted, formatted

    # also when another exception was handled in between
    ast.parse(
        "try:\n    raise StopIteration('needle')\nexcept StopIteration as exc:\n    try:\n"
        "        raise ValueError('inner')\n    except ValueError:\n        raise TypeError('outer') from exc\n"
    )
    with pytest.raises(TypeError, match="outer") as excinfo:
        await ast.eval()
    formatted = "".join(EvalExceptionFormatter(excinfo.value).format())
    assert formatted.startswith(
        "  File \"test\", line 2, in None\n    raise StopIteration('needle')\nStopIteration: needle\n\n"
    )
    assert "RuntimeError" not in formatted, formatted

    # but not after the handler has finished, a documented limit
    ast.parse(
        "try:\n    raise StopIteration('needle')\nexcept StopIteration as exc:\n    saved = exc\n"
        "raise ValueError('later') from saved\n"
    )
    with pytest.raises(ValueError, match="later") as excinfo:
        await ast.eval()
    formatted = "".join(EvalExceptionFormatter(excinfo.value).format())
    assert formatted.startswith("StopIteration: needle\n\nThe above exception was the direct cause"), (
        formatted
    )

    # in a generator expression it becomes a RuntimeError, as in Python, with the script lines
    ast.parse("def boom():\n    raise StopIteration('needle')\n\nlist(boom() for x in [1])\n")
    with pytest.raises(RuntimeError, match="generator raised StopIteration") as excinfo:
        await ast.eval()
    formatted = "".join(EvalExceptionFormatter(excinfo.value).format())
    assert (
        "  File \"test\", line 2, in boom\n    raise StopIteration('needle')\nStopIteration: needle\n\n"
        in formatted
    )
    assert "StopIteration: needle\n\nThe above exception was the direct cause" in formatted, formatted
    assert formatted.endswith("RuntimeError: generator raised StopIteration\n"), formatted

    # the error that task.create() logs
    caplog.clear()
    ast.parse("def func(value):\n    raise StopIteration(value)\n\nresult = await task.create(func, 6)\n")
    await ast.eval()
    assert global_ctx.global_sym_table["result"] is None
    assert (
        'File "test.func", line 2, in func\n    raise StopIteration(value)\nStopIteration: 6\n'
        in caplog.text
    )
    assert "RuntimeError" not in caplog.text

    await Function.waiter_sync()
    await Function.waiter_stop()
    await Function.reaper_stop()
