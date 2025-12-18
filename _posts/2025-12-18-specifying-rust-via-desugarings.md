---
title: "Specifying Rust via Desugarings"
date: 2025-12-18 21:48 +0100
---

> In a recent meeting, [Niko](https://github.com/nikomatsakis) proposed that we should add language
features to enable desugaring more things into valid Rust. This blog post is me taking his idea and
running with it.

How does one specify a language like Rust? At the base of understanding a Rust program today, we
have the [MIR](https://rustc-dev-guide.rust-lang.org/mir/index.html). It's a representation of
a program as a control-flow graph (i.e. with gotos between blocks) and basic operations like
"compute `a+b` and store it in `c`" or "call this function with these arguments".

MIR is simple enough that its semantics can be formally described, and in fact the
[MiniRust](https://github.com/minirust/minirust) project aims to do just that. So, given a MIR
program, we can[^1] know exactly what it means to run it, and all the rest of the compilation to
machine code is but an implementation detail.

The question then is how we get from a Rust program to its MIR. Today a lot of that is hard to
explain; the idea of this blog post is that we would like to explain it as a desugaring, into code
that is also valid Rust yet precise enough that the mapping from it to MIR is obvious[^2].

What features are we missing to make this desugaring possible? Here are some ideas. (Disclaimer:
none of these are my ideas[^8], but instead things I've seen floating around in the Rust ideaspace).

## Identifier hygiene

Macros have [hygiene](https://doc.rust-lang.org/reference/macros-by-example.html?#hygiene), which
avoids accidentally shadowing names:
```rust
let x = 1;
macro_rules! check {
    () => { assert_eq!(x, 1); }; // Uses `x` from the definition site.
}
let x = 2;
check!(); // the test passes
println!("{x}") // prints 2
```

To desugar that to valid rust code, we would need to make this information explicit[^3], maybe by
renaming identifiers or providing extra disambiguation information:
```rust
let x#1 = 1;
let x#2 = 2;
assert_eq!(x#1, 1); // the test passes
println!("{x#2}") // prints 2
```

## Non-dropping assignment

The assignment `*x = y;` does two things: it drops the previous value inside `*x`, then writes `y`
to it. To desugar this we need to make the drop explicit, but then we need something new to express
"an assignment that doesn't drop first".
```rust
*x = y;
// desugars to:
drop(*x);
*x ??? y; // would need a special assignment operator that doesn't drop first
```

One possible solution would be for the borrow-checker to track that `*x` has been moved out and
therefore `*x = y;` no longer needs to drop the value. This already happens if we had a local
variable instead of `*x`, so why not. The trouble is that determining whether a drop is required is
non-trivial, which is in tension with our goal of "the mapping to MIR is obvious".

## Enum projections

In this example, we're taking a borrow inside an enum:
```rust
let opt: &mut Option<u32> = ...;
if let Some(x) = opt {
    // x: &mut u32 here
}
```

The meaning of this is simple: we first check the enum variant, then borrow the right subplace.
We're only missing syntax for that subplace:
```rust
let opt: &mut Option<u32> = ...;
let discriminant = std::mem::discriminant(&*opt);
if discriminant == discriminant_of!(Option<u32>, Some) {
    let x = unsafe { &mut (*opt).Some.0 };
}
```

I'd propose `<place>.<variant_name>` as a syntax, and that would be an unsafe operation because you
must have checked that the variant is the right one first.

## Phased initialization

How does one construct a new value? Built-in values have their built-in constructors (`42`, `true`,
`67.5`, `&mut x`). For structs, enums and unions we have constructors like `Struct { field: 42 }`,
which can be desugared into assignments to the individual fields. Borrowck could easily figure out
that the value is initialized once all the fields are written to[^3] :
```rust
let x = Struct { field: 42 };
// would desugar to:
let x: Struct;
x.field = 42;
// Here `x` can be used normally
```

For enums we can use the enum projections above, combined with a way to set the discriminant (I
quite like [this syntax](https://github.com/rust-lang/rfcs/pull/3607) combined with [these
semantics](https://github.com/rust-lang/rfcs/pull/3727):
```rust
let x: Option<u32>;
unsafe {
    x.Some.0 = 42;
    x.enum#discriminant = discriminant_of!(Option<u32>, Some));
    // The discriminant is only allowed to be set once the rest of the fields are
    // initialized, so borrowck can use that to know that `x` is initialized now.
}
```

I wonder if we could allow this in safe code somehow.

## Naming the return place

Not strictly necessary but fun: the expression `return x` mixes two things: a control-flow construct
and setting the return value. We could separate the two by making the return place nameable:
```rust
fn foo() -> u32 {
    if check() {
        return 42;
    }
    0
}
// would become:
fn foo() -> u32 {
    if check() {
        return#place = 42;
        return;
    }
    return#place = 0;
}
```

No idea of a good syntax here. The way I imagine this working is that the return place starts out
uninitialized, like `let x;`, and a plain `return` is allowed if borrowck determines that the return
place has been initialized.

## Explicit pointer metadata and explicit vtables

Pointer metadata for unsized types is handled invisibly today:

```rust
trait Trait {
    fn method(&self);
}

struct Struct;
impl Trait for Struct {
    fn method(&self) {
        println!("hi!")
    }
}

let x: Struct = ...;
let x: &dyn Trait = &x; // now the pointer carries a pointer to the right vtable.
```

We could make that explicit in two parts: making metadata explicit, and making vtables explicit:

```rust
struct TraitVTable {
    method: unsafe fn(&dyn Trait),
}

static ImplTraitForStructVTable: TraitVTable = TraitVTable {
    // Safety: only call if `this` actually points to a `Struct`.
    method: |this: &dyn Trait| {
        let this: &Struct = unsafe { transmute(this) };
        this.method()
    }
};

// Assuming a method like `std::ptr::from_raw_parts` but for references:
let x: &dyn Trait = unsafe { std::ref::from_raw_parts(&x, &ImplTraitForStructVTable) };
```

How does this extend to e.g. `Rc<Struct> -> Rc<dyn Trait>`? Unclear, one option would be to add
a method to the (unstable) `CoerceUnsize` trait that would contain auto-generated code that unpacks
the pointer, adds the vtable metadata, and repacks it.

## `continue` operator for matches

Operationally, pattern matching expressions just stand for a series of comparisons of discriminants
or integers. So in principle we could desugar a `match` to a big series of `if`s. However that would not
give us the same MIR as we get today: the lowering of patterns to MIR is a bit sophisticated[^6], to
emit more performant code. Let's try to preserve that.

Let's start with match guards:
```rust
match opt {
    Some(x) if foo(x) => ..,
    None => ..,
    Some(_) => ..,
}
```
In that match, if `foo(x)` returns `false` we keep trying the arms below. We could give a syntax for
that:
```rust
'a: match opt {
    Some(x) => if foo(x) {
        ..
    } else {
        continue 'a; // tries the arms after this one
    },
    None => ..,
    Some(_) => ..,
}
```
This is interestingly not more expressive than today's matches, since all of that code could have
just been in the match guard[^4]. Match exhaustiveness would simply ignore arms that use `continue`,
just like it ignores arms with a guard today.

For more complex matches, we can reuse `continue` to decompose them into layers:
```rust
match val {
    (Some(x), None) => .., // branch 1
    (_, Some(y)) => .., // branch 2
    (None, None) => .., // branch 3
}
// could desugar to:
// This shape might look weird but that's how this `match` expression is compiled to
// MIR today. It has that shape to avoid duplicating branch 2.
'a: match val.0.enum#discriminant {
    discriminant_of!(Option, Some) => match val.1.enum#discriminant {
        discriminant_of!(Option, None) => {
            let x = val.0.Some.1;
            // branch 1
        }
        discriminant_of!(Option, Some) => continue 'a,
    },
    _ => match val.1.enum#discriminant {
        discriminant_of!(Option, Some) => {
            let y = val.1.Some.1;
            // branch 2
        }
        discriminant_of!(Option, None) => match val.0.enum#discriminant {
            discriminant_of!(Option, None) => .., // branch 3
            _ => unsafe { unreachable_unchecked() },
        },
    },
}
```

Can we desugar all matches that way? In principle yes because we can do arbitrary[^7] control-flow using
`break`, but that can get ugly:
```rust
match val {
    (Some(x), None) | (None, Some(x)) => .., // branch 1
    (Some(_), Some(_)) | (None, None) => .., // branch 2
}
// naively desugars to:
match val.0.enum#discriminant {
    discriminant_of!(Option, Some) => match val.1.enum#discriminant {
        discriminant_of!(Option, None) => {
            let x = val.0.Some.0;
            // branch 1
        }
        discriminant_of!(Option, Some) => .., // branch 2
    },
    discriminant_of!(Option, None) => match val.1.enum#discriminant {
        discriminant_of!(Option, Some) => {
            let x = val.1.Some.0;
            // also branch 1
        }
        discriminant_of!(Option, None) => .., // also branch 2
    },
}
// to avoid duplication, could do something like:
'match_end: {
    'branch1: {
        'branch2: {
            match val.0.enum#discriminant {
                discriminant_of!(Option, Some) => match val.1.enum#discriminant {
                    discriminant_of!(Option, None) => {
                        let x = val.0.Some.0;
                        break 'branch1;
                    }
                    discriminant_of!(Option, Some) => break 'branch2,
                },
                discriminant_of!(Option, None) => match val.1.enum#discriminant {
                    discriminant_of!(Option, Some) => {
                        let x = val.1.Some.0;
                        break 'branch1;
                    }
                    discriminant_of!(Option, None) => break 'branch2,
                },
            }
        }
        // code for branch 2
        break 'match_end;
    } 
    // code for branch 1
    break 'match_end;
}
```

I don't have great ideas here.

## Etc

What I would love to see eventually is a `cargo desugar` command[^5], or a code action in my editor,
that shows the fully desugared version of a piece of code. Then we'd make the Reference describe
these desugarings, we'd make Miri run on this subset of Rust instead of on MIR, and understanding
the behavior of a piece of Rust code would become much easier!

That's a bunch of ideas, there a likely a lot of other implicit transformations that can't be
expressed in plain Rust. Please share your ideas!


[^1]: MiniRust is not normative yet so that's not strictly true, but most likely something like it will become normative eventually.
[^2]: The Rust Reference could then describe these desugarings and formally specify the desugared subset of Rust, instead of needing a different representation like MIR.
[^3]: I'm sure I recall an RFC/pre-RFC for that but I can't find it, plz let me know if you do.
[^4]: Well, we do put restrictions on match guards, in particular they may not change the scrutinee expression. We'd need to make that restriction explicit as part of the desugaring but I don't know how exactly.
[^5]: I found a [`cargo inspect`](https://github.com/mre/cargo-inspect) tool that does some of that! From the other end, my job project [Charon](https://github.com/AeneasVerif/charon) takes MIR and reconstructs that kind of simple code, for purposes of analysis.
[^6]: It's not that sophisticated actually, e.g. on the second example below it could figure out that matching on `val.1` first produces better code. But we don't do that kind of reasoning yet.
[^7]: Arbitrary [reducible](https://en.wikipedia.org/wiki/Control-flow_graph#Reducibility) control-flow I should say.
[^8]: Except the match-`continue` statement, that one I came up with on my own :3
