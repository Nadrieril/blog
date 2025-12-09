---
title: "The Implicit Magic of Places"
date: 2025-12-06 20:32 +0100
---

Josh Triplett and I are trying to revive [postfix
macros](https://github.com/rust-lang/rfcs/pull/2442), and this raises interesting questions about
places. Places are a part of the language where a lot of implicit things happen, so we need good
vocabulary to talk about them.

In this post I'll give a brief overview of what places are, the implicit operations that surround
them, and the vocabulary we have for them. I'll talk more about postfix macros in a later post.

A lot of this blog post is a retelling of [this blog post from
Ralf](https://www.ralfj.de/blog/2024/08/14/places.html) in my own words; do check out his post if
you want a more rigorous presentation or are interested in how this is relevant for unsafe code.

## Places and place expressions

Rust expressions typically evaluate to a value: to evaluate `x + 1` we first evaluate `x` to its
value, say `42`, then compute `42 + 1` which results in the value `43`. But we don't evaluate `&mut
x` to `&mut 42`, that would make no sense. We want the result of `&mut x` to be about `x` as
a memory location, not `x` as a value.

This is what places are: a place is a memory location, and some Rust expressions refer to places.
We saw that a local variable `x` denotes a place, whereas `42` only denotes a value.

Rust expressions are of two kinds: they denote either a place or a value[^1]. We call them "place
expressions" or "value expressions" depending on which. The following are all the place expressions
in Rust:
- `<ident>`, where the ident is the name of a local variable or static;
- Deref `*<expr>`;
- Field access `<expr>.field`;
- Indexing `<expr>[<expr>]`.

All the other expressions (e.g. method call `<expr>.method()`, arithmetic operation `<expr> + <expr>`,
constant `42`, etc) are value expressions.

On the other side of this, each operation takes either a place or a value. Each operand of an
operation is either a "place context" or "value context" depending on which. The criterion is,
roughly: if the operation cares only about the value of that expression then it's a value context,
if it also cares about where the value is stored then it's a place context.

The following operations are all place contexts:
- Borrows `&mut <expr>`, `&<expr>`, `&raw const <expr>`, `&raw mut <expr>`;
- The LHS[^2] of a write assignment `<expr> = ...;`;
- The RHS of an assignment `let ... = <expr>;`, `... = <expr>;`[^3];
- The scrutinee of a match `match <expr> { ... }`;
- The LHS of a field access `<expr>.field`;
- The LHS of an indexing operation `<expr>[...]`;
- The LHS of a method call `<expr>.method(..)`.

The following operations are all value contexts:
- `<expr> + <expr>`, `<expr> - <expr>` etc;
- `*<expr>`;
- `(<expr>, <expr>)` as well as struct and enum constructors;
- `{ <expr> }`.

These lists are not exhaustive.

[^1]: For extra detail, you may enjoy [the corresponding section of the Reference](https://doc.rust-lang.org/reference/expressions.html#place-expressions-and-value-expressions)
[^2]: "LHS" stands for "left-hand-side" and "RHS" for "right-hand-side".
[^3]: You might think this is a value context because `let x = <expr>;` does cause place-to-value coercion. The trick is "patterns": `let ref mut x = <expr>;` does the same as `let x = &mut <expr>`, which is very much a place context. And you can mix it up: `let (ref mut x, y, _) = <expr>;` does one value-to-place coercion for `y` and considers the rest of `<expr>` as a place.


## Place-to-value coercion

So what happens when you put a place expression in a value context? Rust inserts an implicit read of
the value inside the place. This is called "place-to-value coercion" and following Ralf I'll write
it "`load`":

```rust
let z = x + y + 1; // `x` and `y` are place expressions in a value context
// actually means:
let z = load x + load y + 1;

let x = Some(*ptr); // `*ptr` is a place expression in a value context
// actually means:
let x = Some(load *ptr);
```

If the place expression has a non-`Copy` type, then place-to-value coercion will move the value out
(or raise an error).
E.g.:
```rust
let x = Box::new(42);
let y = Some(x);
// actually means:
let y = Some(load x); // this moves out of `x`
```
In fact whenever you get the "cannot move out of a shared reference" error, you know there was
a place-to-value coercion somewhere.


## Value-to-place coercion

How about the other way around, can a value expression be put in a place context? Absolutely, and we
then get "value-to-place coercion", also called "storing the value in a temporary place". A simple
example is:

```rust
let x = f(&String::from('🦀'));
// actually means something like:
let x = {
    let tmp = String::from('🦀');
    f(&tmp)
};
```

This actually happens quite often, with method autoref (which I'll go into in a moment), e.g.:
```rust
if x.method().is_some() {
    ...
}
// method resolution + autoref desugars this to:
if Option::is_some(&x.method()) { // `x.method()` is a value expression in a place context
    ...
}
```

This direction of coercion is much trickier than the other, because it raises the thorny question of
how long that implicit temporary place should live.
That topic is called "temporary lifetime extension rules" and you should check out [Mara's blog post
on the topic](https://blog.m-ou.se/super-let/)[^4] to get a sense of the space.

[^4]: As it says there, that blog post was part of the discussion around temporary lifetime changes for Rust's 2024 edition. Edition 2024 is now the default one, so part of the rules presented in that post have now changed. [Here](https://github.com/rust-lang/rfcs/pull/3606) is for example on such change.


## Autoderef

Autoderef is, to start with, what allows you to write things like `x.field` when `x: &T`. The
compiler will desugar this to `(*x).field`. This works with any number of references: `x: &&mut &T`
gives `(***x).field`.

This also happens on method calls, inside function arguments (you can pass a `&&mut &T`
to a function expecting a `&T`), and a bunch of other cases I couldn't list exactly[^7].

Autoderef is in fact more powerful than this: it applies not only to built-in references but to any
smart pointer that implements `Deref`. So if `x: Box<T>`[^5], `x.field` becomes
`(*Box::deref(&x)).field` (note the automatic borrow of `x`). Well, unless you're about to use
`x.field` mutably, in which case it becomes `(*Box::deref_mut(&mut x)).field`.

And this is where autoderef is very magical: if `x` is a smart pointer then `*x` by itself is
a place, whose type is known, but that we won't know how to compute until we know what we're doing
to the place. A mutable borrow or assignment causes `deref_mut` to be called, otherwise `deref` is
called[^6].

[^5]: Well `Box` doesn't actually use `Deref`/`DerefMut` because it's built into the borrow-checker, but that's the easiest type to use for illustration so forgive me.
[^6]: And well `Box` also supports moving out of fields, which is deeply magical and which we'll ignore here.
[^7]: I think the rule is that it happens at [coercion sites](https://doc.rust-lang.org/reference/type-coercions.html?#r-coerce.site).


## Autoref and method resolution

The final piece of the puzzle is what happens on method calls. This might be the most magical
desugaring we have: method resolution. Two things happen for `<expr>.method()`: we have to figure
out what method to call, and in the process may have to change `<expr>`.

Take a simple example:
```rust
let mut x = Some(42);
let y = x.take();
```
Here `x` has type `Option<i32>`, so we look at all the methods on `Option` and find `fn take(&mut
self)`. To make the type match, we insert a borrow of `x`. The desugared call is `Option::take(&mut
x)`. This process of "adding extra references when needed" is called "autoref".

Method resolution can also involve autoderef: if `x: Rc<i32>`, `x.is_some()` will first look for an
`is_some` method on `Rc`, then fallback to autoderef and try again. We end up with
`Option::is_some(Rc::deref(&x))`. The [full
algorithm](https://doc.rust-lang.org/reference/expressions/method-call-expr.html#r-expr.method.autoref-deref)
involves a mix of autoderef and autoref.


## Closure capture

Closures add a layer of magic to places: if you mention inside a closure a place that comes from
outside the closure, the place will automatically get carried around along with the closure. We say
the place is "captured", and this means either that we place-to-value coerce and store the resulting
value inside the closure, or that we store a `&` or `&mut` borrow of the place inside the closure.
Much like for autoderef, the way we capture `x` depends on how the place is used.

For example, this:
```rust
let x: Foo = ...;
let f = || {
    x.field.is_some()
};
```
causes `x.field` to be captured, in this case as a shared borrow. The resulting code is equivalent
to the following, where we make the closure object explicit:

```rust
struct Closure<'a> {
    p: &'a Field,
}
impl Fn<()> for Closure<'_> { // I'm cheating a bit on the shape of this trait
    type Output = bool;
    fn call(&self) -> bool {
        Option::is_some((*self).p)
    }
}
// We store a borrow of `x.field` inside the closure.
let f = Closure { p: &x.field };
```

The rules for what we capture exactly are subtle, see [the
Reference](https://doc.rust-lang.org/reference/types/closure.html#capture-modes) for details.

## Conclusion

In this whirlwind tour, we saw that places are at the center of a number of implicit operations:
- place-to-value coercion;
- value-to-place coercion (with temporary lifetime extension);
- autoderef;
- autoref along with method resolution;
- closure capture.

Places get implicitly borrowed, created, moved out of and discarded all of the time implicitly. This
all comes together to "just work" most of the time, and I'd say play a big role in Rust's renowned
expressivity power, but is far from obvious when you start digging.

I hope this post gave you a clearer picture, and I for one know I'll be referencing this blog post
in the future. I expect to keep it up-to-date/add more detail to it, more like a reference document
than a one-off blog post.
