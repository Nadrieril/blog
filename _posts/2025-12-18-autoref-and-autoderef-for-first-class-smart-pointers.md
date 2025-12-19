---
title: "Autoref and Autoderef for First-Class Smart Pointers"
date: 2025-12-18 02:18 +0100
---

> This blog post is part of the discussions around the [Field Projections project
goal](https://github.com/rust-lang/rust-project-goals/issues/390). Thanks to Benno Lossin and
everyone involved for the very fruitful discussions!

In a [my first post on this blog][original_post] I outlined a solution for making custom smart pointers as well
integrated into the language as references are today. I had left the exact rules for autoref and
autoderef unspecified; this blog post is my attempt to write them down precisely.

The basic tenets I have for the whole feature are:
- Expressions have a type that doesn't depend on their context.
- To understand what operation (e.g. method call) is being done on an expression, one only needs to know
  the type of that expression.

## `PlaceWrap` and non-indirected place operations

One of the recent ideas we've added to the proposal is this trait[^5], which we'll need to explain
the desugarings:
```rust
/// If `X: PlaceWrap` and `X::Target` has a field `field`, then `X` itself acquires a virtual field
/// named `field` as well. That field has type `<X as
/// PlaceWrap<proj_ty!(X::Target.field)>>::Wrapped`, and `WrappedProj` is the
/// projection used when we refer to that field.
pub unsafe trait PlaceWrap<P: Projection<Source = Self::Target>>: HasPlace {
    /// The type of the virtual field. This is necessarily a transparent wrapper around `P::Target`.
    type Wrapped: HasPlace<Target = P::Target>;
    type WrappedProj: Projection<Source = Self, Target = Self::Wrapped>;
    fn wrap_proj(p: &P) -> Self::WrappedProj;
}
```

This is implemented for "non-indirected containers" such as `MaybeUninit`, `RefCell`, `Cell`, etc.
What it does is that if `Struct` has a field `field: Field`, then `MaybeUninit<Struct>` has
a virtual field `field: MaybeUninit<Field>`.

In the next section I explain how that interacts with the existing place operations, and at the end
we'll see examples of how they work together for very nice expressivity.

To explain the computations I propose a strawman syntax `@@Type p` which is allowed iff `Type` is
a transparent wrapper around the type of `p`. This expression is a place expression too, it behaves
like basically a transmute of the target without doing anything else. In particular this is how
`PlaceWrap` operates: if `x: MaybeUninit<Struct>`, `x.field` desugars to `@@MaybeUninit (*x).field`.

[^5]: Thanks to Benno for formulating it like this! Compared to [our official version](https://github.com/rust-lang/rust-project-goals/issues/390#issuecomment-3659055067) I made the `Wrapped` type separate because this makes explanations easier than using `WrappedProj::Target` all the time.

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
- If `T: !HasPlace`, error.
- If `T: HasPlace`, we first descend through `T::Target::Target::etc` until we find a type that has
  a field `field: F`. We get the intermediate expression `tmp_place := (****p).field: F` with the appropriate
  number of derefs.
- We then "go back up" as long as the intermediate `T::Target::etc` implements `PlaceWrap<the_right_thing>`.
  Every time we go back up in such a way, we wrap our target place in `tmp_place := @@Wrapped tmp_place`.
- The first time we can't `PlaceWrap`, we're done.
- If `T: !HasPlace`, error.

Finally, indexing is easy because we're only talking about built-in indexing here. It's exactly like
a field access, where `[T]` and `[T; N]` have one field per index. The tricky part is just that  the
index is not known at compile-time. That's the reason why `Projection`s don't make the offset
available as a `const` actually.

Examples, assuming `Struct` has a field `field: Field`:
- `p: MaybeUninit<Struct>`: `p.field` desugars to `@@MaybeUninit (*p).field` with type `MaybeUninit<Field>`;
- `p: MaybeUninit<MaybeUninit<Struct>>`: `p.field` desugars to `@@MaybeUninit @@MaybeUninit
  (**p).field` with type `MaybeUninit<MaybeUninit<Field>>`;
- `p: &&&MaybeUninit<Struct>`: `p.field` desugars to `@@MaybeUninit (****p).field` with type `MaybeUninit<Foo>`;
- `p: MaybeUninit<&Struct>`: `p.field` desugars to `(**p).field` with type `Foo`[^1];
- `p: MaybeUninit<[u8]>`: `p[42]` desugars to `@@MaybeUninit (*p)[42]` with type `MaybeUninit<u8>`.

Note that because we resolve place expressions one operation at a time, we ensure that e.g. `p.a.b`
is always the same as `(p.a).b`.

## Computing the type of borrows

Let `p` be a place expression of type `T`. The type of `@Ptr p` is easy: it's always
`Ptr<Something>`, with the guarantee that `Ptr<Something>: HasPlace<Target=T>`. This means `p`
cannot change type when this happens. There is no extra autoderef or anything at this stage.

## Method autoref

In this section, I will assume that `T: Receiver` => `T: HasPlace<Target=<T as
Receiver>::Target>>`[^3] and that `T: Deref` => `T: HasPlace<Target=<T as Deref>::Target>>`.

Let `p` be a place expression of type `T`, and assume we want to typecheck `p.method()`. We first
compute the set `{T, T::Target, T::Target::Target, ..}` as long as the types implement `HasPlace`.

For each such type `U`, we look through all the `impl U` and `impl Trait for U` for a method with
the right name. This gives us a list of "method candidates".
If there are none, error; if there are several, pick one in some way. Which one to pick is important
for ergonomics but irrelevant for us now.

If the selected method takes `fn method(self, ..)` directly, we desugar to `<..>::method(***p)`
(with enough derefs to get to the right type) and we're done.

Otherwise the method takes `fn method(self: X, ..)` where `X: HasPlace` (by the assumption
on `Receiver` above). If `X::Target` is one of the candidate types above, let `q := ***p` be `p`
suitably derefed to get to that candidate type; we then desugar to `<..>::method(@X q)`.
If `X::Target` is not one of the candidate types, we go back and pick another method.

This draft is possibly quite naive, I've heard that method resolution is quite tricky. Whatever
I might be missing, the core ideas I'm trying to convey are this:
1. We only ever consider the type of the place. The pointer the place came from does not come into
   play until after we've desugared, to check if the borrow was allowed after all;
2. We search only impl blocks for `T`, `T::Target`, `T::Target::Target`, etc.
3. This works wonderfully with [`arbitrary_self_types`]: when we find an arbitrary self type we can
   just attempt to borrow with that pointer. This means e.g. that for `x: CppRef<Struct>` and `fn
   method(self: CppRef<Self>)` on `Field`, `x.field.method()` Just Works.

## Desugaring the place operations

Recall that the operations we can do on a place are: borrow, read, write, in-place-drop[^6]. Each
of these comes with a corresponding `PlaceOp` trait. Once we know which operation to do on the
place, we can desugar the operation to a call to the appropriate trait method, which will also check
if that operation is allowed by the pointer in question.

Let's desugar a `PlaceOp` operation on a place `p`.
A place expression is made of three things: locals, derefs and projections, where
"projections" means field accesses, indexing, and either of these mediated by `PlaceWrap`.

So our place `p` can always be written as `p = q.proj` where `.proj` represents all the
non-indirecting projections (including `PlaceWrap` ones), and `q` is a place expression that's
either a local or a deref. Let `U` be the type of `q`. Then an operation on `p` desugars to
`PlaceOp::operate(get!(q), proj_val!(U.proj))`, where `get!` is defined as:
- if `q` is a local, `get!(q)` is `&raw const @@LocalPlace q`;
- otherwise `q` is a deref which we can write `*(r.proj2)`, and we can get the right pointer using
  `PlaceDeref::deref`[^2]. This applies recursively if `r` itself contains a deref, etc.

Where `PlaceWrap` comes into play is in this `proj_val!` macro: that macro computes the value of the
appropriate `P: Projection` type. If `PlaceWrap` is involved, then it will be used in computing that
projection.

## Canonical reborrows

As a special case of the borrows above, the official proposal includes a notion of ["canonical
reborrows"](https://github.com/rust-lang/rust-project-goals/issues/390#issuecomment-3644702112),
whereby each pointer can declare the default type with which to be reborrowed, and the (possibly
temporary) syntax `@$place` uses it.

The way it works is simple: `@$place` desugars just like `PlaceBorrow` above, except when we get to
`PlaceOp::operate` we use `<PlaceBorrow<'_, _, <Ptr as
CanonicalReborrow<proj_ty!(U.proj)>>::Output>>::borrow` where `Ptr` is the type of `*get!(q)`. This
is equivalent to `@Output $place` with that same `Output` type.

## Putting it all together

Let's go through a bunch of examples. In what follows `e` is the expression of interest that we want
to desugar and typecheck. We also assume the obvious place operations on standard library types, as
well as:
```rust
struct Struct {
    field: Field,
}
struct Field {
    value: u32,
}

// Implements `PlaceWrap`.
struct W<T> {
  value: PhantomData<()>,
  wrapped: T,
}
```

1. `p: &mut MaybeUninit<Struct>`, `e := &mut p.field`

    We get `e = &mut @@MaybeUninit (**p).field : &mut MaybeUninit<Field>`, and the two traits involved
    are `PlaceWrap` for `MaybeUninit` and `PlaceBorrow<P, &mut P::Target>` for `&mut P::Source`. Note
    how `&mut` is entirely unaware of anything special happening, and how that would work with many
    nested wrappers.

2. `x: Struct`, `impl Field { fn method(self: CppRef<Self>) }`, `e := x.field.method()`

    We get `e = Field::method(@CppRef x.field)`. Per the section on borrows, `@CppRef x.field`
    becomes `@CppRef (*@@LocalPlace x).field`, which is allowed iff `LocalPlace<Struct>:
    PlaceBorrow<P, CppRef<Field>>`. The smart pointer can opt-in to that, and of course they can
    choose the nature of the resulting borrow (owning, exclusive, shared, etc).

3. `x: &mut CppRef<Struct>`, `impl Struct { fn method(self: &CppRef<Self>) }`, `e := x.method()`

    We get `e = Struct::method(&*x)`.

4. `x: &mut CppRef<Struct>`, `impl Field { fn method(self: &CppRef<Self>) }`, `e := x.field.method()`

    I made this an error, but in theory we could desugar this to `Field::method(&(@CppRef
    (**x).field))`, i.e. create a temporary `CppRef` and borrow that. We'll pick whatever's
    consistent with the rest of Rust I guess.

5. `x: W<Struct>`, `e := w.field.value`

    We get `e = (@@W (*x).field).value : PhantomData<()>` because the real field on `W` takes
    precedence over the virtual field. If we wanted to access the `value` field of `Field`, we'd
    have to write `@@W (*w).field.value`.

6. `x: &Box<Arc<Struct>>`, `impl Field { fn method(self: ArcRef<Self>) }`, `e := x.field.method()`

    We get `e = Field::method(@ArcRef (***x).field)`. The final desugaring looks like:
    ```rust
    let tmp: &raw const LocalPlace<&Box<Arc<Struct>>> = &raw const @@LocalPlace x;
    let tmp: &raw const &Box<Arc<Struct>> = <LocalPlace<_> as PlaceDeref<_>>::deref(tmp, trivial_proj_val!(&Box<Arc<Struct>>));
    let tmp: &raw const Box<Arc<Struct>> = <&_ as PlaceDeref<_>>::deref(tmp, trivial_proj_val!(Box<Arc<Struct>>));
    let tmp: &raw const Arc<Struct> = <Box<_> as PlaceDeref<_>>::deref(tmp, trivial_proj_val!(Arc<Struct));
    let arc_ref: ArcRef<Field> = <PlaceBorrow<'_, _, ArcRef<_>>>::borrow(tmp, proj_val!(Struct.field));
    Field::method(arc_ref)
    ```

    Note how only the last deref (the one of `Arc`) is involved in the reborrow. The rest are just
    `PlaceDeref`ed through.

7. `x: Arc<Box<Struct>>`, `e := @ArcRef x.field`

    That's an error. We get `e = @ArcRef (**x).field`, which uses `Arc as PlaceDeref` then `Box as
    PlaceBorrow<'_, _, ArcRef<_>>` which doesn't exist. This is unfortunate because in principle we
    can make this `ArcRef<Field>`. But this would need something like `Arc<Box<Struct>> as
    PlaceBorrow<'_, P, ArcRef<Field>>` where `P` includes a deref. Projections are just an offset in
    our model currently, so that's not allowed[^5].

8. `x: &Arc<[Struct]>`, `e := @x[42].field`

    This desugars to `@ArcRef x[42].field`. The final desugaring looks like:
    ```rust
    let tmp: &raw const LocalPlace<&Arc<[Struct]>> = &raw const @@LocalPlace x;
    let tmp: &raw const &Arc<[Struct]> = <LocalPlace<_> as PlaceDeref<_>>::deref(tmp, trivial_proj_val!(&Arc<[Struct]>));
    let tmp: &raw const Arc<[Struct]> = <&_ as PlaceDeref<_>>::deref(tmp, trivial_proj_val!(Arc<[Struct]));
    let arc_ref: ArcRef<Field> = <PlaceBorrow<'_, _, ArcRef<_>>>::borrow(tmp, proj_val!([Struct][42].field));
    arc_ref
    ```

    Note again how the last derefed pointer is the one used to determine the reborrow.

Below are the footnotes, this theme does not distinguish them very clearly:

[original_post]: https://nadrieril.github.io/blog/2025/11/11/truly-first-class-custom-smart-pointers.html
[`arbitrary_self_types`]: https://rust-lang.github.io/rfcs//3519-arbitrary-self-types-v2.html
[PlaceDeref]: https://github.com/Nadrieril/place-projections-demo/blob/a5a73414dec04370b15733b6eef6cb4215ddff6d/src/place_ops.rs#L66-L75
[^1]: This place looks like it should be illegal but there may be wrappers for which it is usable. For `MaybeUninit` this will just be unusable because `MaybeUninit` does not implement `PlaceDeref`.
[^2]: I mentioned the idea of `PlaceDeref` briefly in my [original post][original_post] but hadn't fleshed it out. It's just a `&raw const`-reborrow meant to only be used for nested derefs. See its proper definition [here][PlaceDeref].
[^3]: I'm talking about the `Receiver` trait from the [`arbitrary_self_types`] feature.
[^5]: Also this would make inference more complicated because we'd have to try `PlaceBorrow` for each of the pointers involved, instead of having a deterministic choice like we do today.
[^6]: I'm not counting deref because deref constructs a new place on which we'll do operations, so we'll always start the desugaring from a non-deref operation.
