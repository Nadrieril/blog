---
title: "Postfix Macros and `let place`"
date: 2025-12-09 22:46 +0100
---

[Postfix macros](https://github.com/rust-lang/rfcs/pull/2442) is the feature proposal that would
allow `something.macro!(x, y, z)`. It's been stalled for a long time on some design issues; in this
blog post I'm exploring an idea that could answer these issues.

The obvious way to make the feature work is to say that in `<expr>.macro!()`, the macro gets the
tokens for `<expr>` and does what it wants with them.

This however allows macros to break the so-called "no-backtracking rule" (coined by Tyler Mandry
IIRC): in `x.is_some().while! { ... }`, reading the `while` makes us realize that the `is_some()`
call wasn't just a boolean value, it was an expression to be evaluated every loop. So we sort of
have to go back and re-read the beginning of the line. For purposes of reducing surprise and code
legibility, we'd like to avoid that.

Hence the question that the feature stalled on: can we design postfix macros that always respect the
no-backtracking rule? We would need to somehow evaluate `<expr>` once and pass the result to the
macro instead of passing `<expr>` itself. Apart from that I'll assume that we want maximal
expressiveness.

This post is centrally about places and the implicit operations that surround them; check out [my
recent blog post on the
topic](https://nadrieril.github.io/blog/2025/12/06/on-places-and-their-magic.html) for an overview
of that vocabulary.

## Partial Place Evaluation

To get the obvious out of the way: we can't just desugar `<expr>.method()` to `let x = <expr>;
x.method()`; that may give entirely the wrong behavior, e.g.:

```rust
struct Foo { count: Option<u32> }
impl Foo {
    fn take_count(&mut self) -> Option<u32> {
        // That's fine
        self.count.take()
        // That creates a copy
        // let tmp = self.count;
        // tmp.take() // modifies the copy instead of the original
    }
}
```

In technical terms, that's because the LHS of a method call is a place expression. Storing
`<expr>` in a temporary adds an incorrect place-to-value coercion. The same applies to postfix
macros.

I think that the behavior we ideally want is to pre-evaluate all temporaries (that arise from
value-to-place coercion), and pass whatever remains of the expression as-is to the macro. I'll call
that "partial place evaluation".

Some examples:
```rust
let x: Foo = ...;
x.field.macro!()
// becomes (there are no temporaries)
macro!(x.field)

impl .. { fn method(&self) -> Foo { .. } }
x.method().field.macro!()
// becomes
let mut tmp = x.method();
macro!(tmp.field)
```

<!-- I think this works, because of two things: -->
<!-- 1. Evaluating the temporaries early and storing them on the side is what compilation would do -->
<!--    anyway, so we don't lose any generality (as long as we're careful about the lifetime of -->
<!--    temporaries); -->
<!-- 2. What remains of the expression is made purely of place operations like derefs and field accesses, -->
<!--    which have no side-effect, so we don't care what the macro does with it. -->

Looks easy enough, except for autoderef[^6].

```rust
let x: Box<Foo> = ...;
x.field.macro!()
```

Depending on the contents of `macro!()`, this may need to expand to a call to `deref` or `deref_mut`:
```rust
let tmp = Box::deref(&x);
macro!((*tmp).field)
// or
let tmp = Box::deref_mut(&mut x);
macro!((*tmp).field)
```

At this point it's hopefully clear that no simple syntactic transformation will give us what we want.

## Place aliases, aka `let place`

What we're trying to express is "compute a place once and use it many times".
`let place` is an idea I've seen floating around[^7] which expresses exactly that:
`let place p = <expr>;` causes `<expr>` to be evaluated as a place,
and then `p` to become an alias for the place in question.
In particular, this does _not_ cause a place-to-value coercion.[^4]

```rust
let place p = x.field; // no place-to-value, so this does not try to move out of the place
something(&p);
something_else(p); // now this moves out
// would be identical to:
something(&x.field);
something_else(x.field); // now this moves out

let place p = x.method().field;
something(&p);
// would be identical to:
let tmp = x.method();
something(&tmp.field);
```

This is exactly what we need for postfix macros: `<expr>.macro!()` would become (using a match to
make the temporary lifetimes work as they should 🤞):
```rust
match <expr> {
    place p => macro!(p),
}
```

This would have the effect I propose above: any side-effects are evaluated early, and then we can do
what we want with the resulting place.

> EDIT: This next paragraph was changed because I initially thought that macro would work :')

One of my litmus tests of expressivity for postfix macros is this `write!` macro, which ~~ends up
working pretty straighforwardly~~ turns out not to work so well:
```rust
macro_rules! write {
    ($self:self, $val:expr) => ({
        $self = $val; // assign to the place
        &mut $self // borrow it mutably
    })
}
let mut x; // borrowck understands that `write!` initializes the place!
let _ = x.write!(Some(42)).take();
// desugars to:
let _ = match x {
    place p => write!(p, Some(42)).take(),
};
// desugars to:
let _ = { write!(x, Some(42)) }.take(); // the match body forces a place-to-value :(
// desugars to:
let _ = {
    x = Some(42);
    (&mut {x}).take()
};
// desugars to:
let _ = {
    x = Some(42);
    let mut tmp = x; // copies `x` :(
    (&mut tmp).take() // not what I wanted :(
};
```
Seems like a `match` isn't the right thing. I'm not sure how to get the right temporary scopes then.

## `let place` and custom autoderef

The hard question is still autoderef[^6] :
```rust
let mut x: Box<Foo> = ...;
let place p = x.field; // should this use `deref` or `deref_mut`?
something(&p);
something_else(&mut p); // causes `deref_mut` to be called above
```

For that to work, we infer for each place alias whether it is used by-ref, by-ref-mut or by-move
(like closure captures I think), and propagate this information to its declaration so that we can
know which `Deref` variant to call [^5].

## `let place` isn't too powerful

Turns out `let place` is a rather simple feature when we play with it:
```rust
// Place aliases can't be reassigned:
let place p = x.field;
// Warning, this assigns to `x.field` here! that's what we want place aliases to do
// but it's admittedly surprising.
p = x.other_field;

// You can't end the scope of a place alias by hand:
let place p = x.field;
drop(p); // oops you moved out of `x.field`
// `p` is still usable here, e.g. you can assign to it

// Place aliases can't be conditional.
let place p = if foo() { // value-to-place happens at the assignment
    x.field // place-to-value happens here
} else {
    x.other_field
};
// This won't mutate either of the fields, `p` is fresh from a value-to-place coercion. I propose
// that this should just be an error to avoid sadness.
do_something(&mut p);
```

In particular it's easy to statically know what each place alias is an alias for.

The caveat is that all of those are surprising if you think of `p` as a variable. This is definitely
not a beginners feature.

## `let place` doesn't need to exist in MIR

The big question that `let place` raises is what this even means in the operational semantics of
Rust. Do we need a new notion of "place alias" in [MiniRust](https://github.com/minirust/minirust)?

I think not. The reason is that the "store intermediate values in temporaries" happens automatically
when we lower to MIR. All place coercions and such are explicit, and MIR place expressions do not cause
side-effects. So whenever we lower a `let place p` to MIR, we can record what `mir::Place` `p`
stands for and substitute it wherever it's used.

To ensure that the original place doesn't get used while the alias is live, we insert a fake borrow
where the `let place` is taken and fake reads when it's referenced. That's already a trick we use
in MIR lowering for exactly this purpose[^3].

So the only difficulty seems to be the mutability inference mentioned in previous section. The rest
of typechecking `let place` is straighforward: `let place p = <expr>;` makes a place with the same
type as `<expr>`, and then it behaves pretty much like a local variable.

All in all this is looking like a much simpler feature that I expected when I started playing with
it.


## `let place` is fun

I kinda of want it just because it's cute. It makes explicit something implicit in a rather elegant
way. Here are some fun things I discovered about it.

To start with, it kind of subsumes binding modes in patterns: `if let Some(ref x) = ...` is the same
thing as `if let Some(place p) = ... && let x = &p`. One could even use `place x` instead of `x` in
patterns everywhere and let autoref set the right binding mode! That's a funky alternative to match
ergonomics.

We can also use it to explain this one weird corner case of borrow-checking. This code is rejected
by the borrow-checker, can you tell why?
```rust
let mut x: &[_] = &[[0, 1]];
let y: &[_] = &[];
let _ = x[0][{x = y; 1}];
//      ^^^^ value is immutable in indexing expression
```
What's happening is that we do the first bound-check on `x` before we evaluate the second index
expression. So we can't have that expression invalidate the bound-check on pain of UB. We can use
`let place` to explain the situation via a desugaring:
```rust
x[0][{x = y; 1}]
// desugars to:
let place p = x[0]; // bounds check happens here
p[{x = y; 1}]
// desugars to:
let place p = x[0];
let index = {x = y; 1}; // x modified here
p[index] // but place alias used again here
```

Can this be used to explain closure captures? I don't think so because closures really do carry
borrows of places, not just places. It does feel like a related kind of magic though.

### Conclusion

I started out writing this blog post not knowing where it would lead, and I'm stoked of how clean
this proposal ended up looking. I kinda want `let place` even independently from postfix macros. The
one weird thing about `let place` is this "mutability inference" for autoderef, hopefully that's an
acceptable complication.

I'm most looking forward to everyone's feedback on this; `let place` is rather fresh and I wanna
know if I missed anything important (or anything fun!).

[^4]: In a way `let place` creates a sort of magic reference to the place: you can move out of it (if allowed), mutate it (if allowed), get shared access to it (if allowed). The "magic" part is that the permissions that the magic reference requires are inferred from how the reference is used, instead of declared up front like for `&`, `&mut` and proposed extensions like `&pin mut`, `&own` and `&uninit`.
[^5]: You might thing this gets more complicated with [custom places and field projections](https://nadrieril.github.io/blog/2025/11/11/truly-first-class-custom-smart-pointers.html), but actually for those we have no choice but to call the appropriate `PlaceOperation` trait method only when we know what operation is being done on the place, so there's no need to infer anything. The question of how to represent this in MIR may get a bit more tricky though.
[^6]: Well `Box` doesn't actually use `Deref`/`DerefMut` because it's built into the borrow-checker, but that's the easiest type to use for illustration so forgive me.
[^3]: See the indexing example below, which I took from the [doc on fake borrows](https://doc.rust-lang.org/nightly/nightly-rustc/rustc_middle/mir/enum.BorrowKind.html#variant.Fake).
[^7]: I can't find references to that idea apart from [this thread](https://rust-lang.zulipchat.com/#narrow/channel/213817-t-lang/topic/let.20place/with/421534614), so maybe I'm the one who came up with it. I can find [this](https://internals.rust-lang.org/t/idea-placepattern/11090) that uses the same syntax but for a different purpose.
