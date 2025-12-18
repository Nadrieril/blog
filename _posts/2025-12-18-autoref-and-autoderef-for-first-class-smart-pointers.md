---
title: "Autoref and Autoderef for First-Class Smart Pointers"
date: 2025-12-18 02:18 +0100
---

> This blog post is part of the discussions around the [Field Projections project
goal](https://github.com/rust-lang/rust-project-goals/issues/390). Thanks to Benno Lossin and
everyone involved for the very fruitful discussions!

In a [my first post on this
blog](https://nadrieril.github.io/blog/2025/11/11/truly-first-class-custom-smart-pointers.html)
I outlined a solution for making custom smart pointers as well integrated into the language as
references are today. I had left the exact rules for autoref and autoderef unspecified; this blog
post is my attempt to write them down precisely.

The basic tenets I have for the whole feature are:
- Expressions have a type that doesn't depend on their context.
- To understand what operation (e.g. method call) is being done on an expression, one only needs to know
  the type of that expression.

## Computing the type of a place

Every place expression starts with a local or a temporary, with a known type. We then apply one or
more of the pure place operations, recursively:
- deref `*p`;
- field access `p.field`;
- indexing `p[i]`.

Deref is simple: `*p` requires that `p: T: HasPlace`, and
then `*p: T::Target`.

Field access is the tricky one; I propose the following. Let `p` be a place expression of type `T`.
- If `T` has a field `field: F`, `p.field: F` and we're done;
- If `T: HasPlace`, we first try place wrapping[^1]: we descend through `T::Target::Target::etc`
  until we find a type that has a field `field: F`. If then all the intermediate `T::Target::etc`
  implement `PlaceWrap<the_right_thing>`, `p.field` gets the type `<T as
  PlaceWrap<the_right_thing>>::WrappedProjection::Output`[^2].
- If `T: HasPlace`, and the above didn't work, `p.field` desugars to `(*p).field` and we recurse on
  resolving this.
- If `T: !HasPlace`, error.

Finally, indexing is easy because we're only talking about built-in indexing here. It's exactly like
a field access, except that the only types that have such a field are `[T]` and `[T; N]`.

Examples, assuming `Struct` has a field `field: Field`:
- `p: MaybeUninit<Struct>`: `p.field: MaybeUninit<Field>`;
- `p: MaybeUninit<MaybeUninit<Struct>>`: `p.field: MaybeUninit<MaybeUninit<Field>>`;
- `p: &&&MaybeUninit<Struct>`: `p.field` desugars to `(***p).field: MaybeUninit<Foo>`
- `p: MaybeUninit<&Struct>`: `p.field` is an error;
- `p: MaybeUninit<[u8]>`: `p[42]: MaybeUninit<u8>`.

Because we resolve place expressions one operation at a time, we ensure that e.g. `p.a.b` is always
the same as `(p.a).b`.

[^1]: I haven't talked about `PlaceWrap` yet, Benno gave a quick mention [here](https://github.com/rust-lang/rust-project-goals/issues/390#issuecomment-3659055067).
[^2]: I'd really like there to be a syntax for this, let's say `@@Wrapper <place>`, which 1. only works for transparent structs and 2. yields a place. In particular this can happen inside a borrow, e.g. `&@@Wrapper p`. With that syntax, the desugaring here would look like `@@Wrapper1 @@Wrapper2 (**x).field`.

## Computing the type of borrows

Let `p` be a place expression of type `T`. The type of `@Ptr p` is easy: it's always
`Ptr<Something>`, with the guarantee that `Ptr<Something>: HasPlace<Target=T>`. This means `p`
cannot change type when this happens. There is no extra autoderef or anything at this stage.

To typecheck that the borrow is allowed is a bit more involved. First we identify the outermost
deref in `p`. If there is one, it's a type `Q: HasPlace`; if there is none we pick `Q:
LocalPlace<U>` were `U` is the type of the local at the root of the place expression. We then check
if `Q: PlaceBorrow<'_, P, Ptr<Something>>`, where `P` is the projection that represents the place
operations that happened between the outermost deref and `p`. These are either field accesses or
built-in indexing, hence valid projections.

If there was more than one deref inside `p`, we also check that each of these dereferenced pointers
implements `PlaceDeref<P>` where `P` is the projection that follows the deref.

## Method autoref

> This is quite naive, I know method resolution is more complicated than that. But hopefully that
sketch points in the right direction.

In this section, I will assume that `T: Receiver` => `T: HasPlace<Target=<T as
Receiver>::Target>>`[^3], and ignore `Deref` entirely. To handle `Deref` sanely I'd also like to
assume that `T: Deref` => `T: HasPlace<Target=<T as Deref>::Target>>`.

Let `p` be a place expression of type `T`, and assume we want to typecheck `p.method()`.

First, look through all the `impl T` and `impl Trait for T` for a method with that name. If there
are multiple, pick one somehow or raise ambiguity. If the method takes `fn method(self, ..)`
directly, we desugar to `<..>::method(p)` and we're done.

Otherwise the method takes `fn method(self: X, ..)` where `X: HasPlace<Target=T>` (by the first
assumption). We therefore desugar to `<..>::method(@X p)`.

If there was no such method, desugar to `(*p).method()` and continue resolving.

Note: this doesn't support autoref for cases like calling a `fn method(self: &CppRef<Self>)` on `p:
CppRef<Self>`. A more clever algorithm could do that by checking the methods on `T::Target` without
derefing so eagerly.

Despite the incompleteness of this draft, the core ideas I'm trying to convey are this:
1. We only ever consider the type of the place. The pointer the place came from does not come into
   play until after we've desugared, to check if the borrow was allowed after all;
2. We search only impl blocks for `T`, `T::Target`, `T::Target::Target`, etc.
3. This works wonderfully with [`arbitrary_self_types`]: when we find an arbitrary self type we can
   just attempt to borrow with that pointer. This means e.g. that for `x: CppRef<Struct>` and `fn
   method(self: CppRef<Self>)` on `Field`, `x.field.method()` Just Works.

Hopefully this sketch is clear enough.

[`arbitrary_self_types`]: https://rust-lang.github.io/rfcs//3519-arbitrary-self-types-v2.html
[^3]: I'm talking about the `Receiver` trait from the [`arbitrary_self_types`] feature.
