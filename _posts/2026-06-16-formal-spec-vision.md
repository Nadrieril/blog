---
title: "A Vision for a Rust Formal Specification"
date: 2026-06-16 16:29 +0200
---

Many people want to know, precisely, which pieces of text are valid Rust programs,
and for those that are, what they do.
This group includes compiler writers, language designers, researchers, `unsafe` code writers,
safety-critical industry assessors, and of course any Rust developer trying to understand a piece of
code.

A document that answers these questions is called a "specification".
If it is precise enough, it's called "formal".
We'd quite like to get one of these.
Even better, an ideal specification would also be:
- Understandable by non-experts;
- Executable, so we can test it on real code;
- Well-structured, so it can be turned into maths and have stuff proven about it;
- Easy to evolve, so it can be at the heart of language evolution instead of an afterthought.

We're not close to having that today,
but lately a couple of efforts have been coming together
that paint a story I find quite compelling.
Allow me to sketch it for you.

## The Core Ingredients: MiniRust, Desugarings, and Trait Proofs

### The Base: MiniRust

[MiniRust] is the backbone of this whole vision.
It's a project led by Pr. Ralf Jung
to precisely specify the runtime (aka dynamic) semantics
of Rust code, which includes precisely answering the
question of what is or isn't [Undefined Behavior][UB] (UB).

MiniRust ticks all the boxes I laid out above:
- It's written in code mixed with English explanations, legible by anyone who knows Rust;
- It takes the form of an interpreter for a simplified Rust, which makes it directly executable. UB
  is simply an exception raised by this interpreter when encountering an invalid program state;
- It is amenable to being turned into maths, I hear there are students working on it;
- It is easy to evolve: [two](https://github.com/amanieu/minirust/commit/90e085552cb8228e499e073669512fb87181ab7b)
  [recent proposals](https://github.com/minirust/minirust/pull/292) for changes to Rust
  semantics came with patches to MiniRust in order to remove the ambiguity inherent in an
  English-text proposal.

It's a real thing you can use today, and it is close to being considered the source of truth
about Rust's runtime semantics[^1].
That part I consider taken care of, and I tip my hat to Ralf for a beautiful vision and execution.

[^1]: In fact one of the questions we have to solve to make this happen is "how to we even make this
  authoritative?". This blog post is my answer to that question.

### From Rust to MiniRust: Desugarings

MiniRust captures a very bare-bones version of Rust:
it has no generics, no patterns, no method call syntax, no dropping at end of scope (in fact, no
scopes), etc.
It's reduced to 1. a control-flow graph, 2. individual statements that each do one simple operation.
This is technically enough to execute any real Rust program,
but MiniRust doesn't describe how to handle any of these advanced features.

Hence the second piece of the puzzle: we need to describe how to go from a real Rust program
to one that MiniRust can execute.
Based on an idea shared by [Niko Matsakis][Niko], I've been exploring the approach
of defining advanced features by translating (*desugaring*)
them into more basic ones.
E.g. pattern-matching can be defined by how it desugars into simple boolean conditions.

To prove that this idea could work, I wrote an [mdbook][rust-via-desugarings]
that sketches 30 or so desugaring steps that transform
a Rust function down to something very close to a MiniRust one.
I propose this as the second half of our spec: a tower of small desugarings
that explain powerful features in terms of simpler ones.

### The Missing Piece: Generics and Trait Proofs

This two-part model (desugarings+MiniRust)
covers a significant part of the Rust language
but leaves one important domain uncovered: generics, and in particular traits.
MiniRust has no notion of traits, and it's also
not obvious how to describe trait solving as a desugaring.

It so happens that the [types team][types-team] has lately been
experimenting with an idea called ["dictionary-passing style"][dict-experiment]
or "explicit trait proofs",
in which the trait solver would keep track explicitly of how it came to conclude that a particular
trait fact holds.

That's the third piece of the puzzle: I propose that
the target of our desugarings should have syntax to explicitly
refer to trait impls and trait bounds, to combine them, to call methods on them etc.
I wrote [this blog post](https://nadrieril.github.io/blog/2026/03/20/dictionary-passing-style.html)
to introduce the idea, and wrote up
[a syntax proposal](https://nadrieril.github.io/rust-via-desugarings/features/explicit-trait-proofs.html)
that covers most of what we should need[^2].

Therefore what we get after desugaring isn't a MiniRust program,
but what I'm uninventively[^3] calling a *PolyMiniRust* program, which
is MiniRust extended with generics and trait proofs.

[^2]: One thing I know is missing at the time of writing is dealing with higher-ranked trait bounds.
[^3]: I'd really like a catchier name, please help :')

## The Complete Specification

Here is what I have in mind:
- The whole spec would be a program written in [specr], the Rust-flavored literate language
  backing MiniRust, with abundant explanatory English text (like MiniRust);
- It would be structured as a book, much like [rust-via-desugarings];
- The first chapter would define the datastructures for our language, e.g. `struct
  FunctionDeclaration`, `enum Type` etc;
- The second chapter would start with parsing text into these datastructures, followed by
  a series of individual desugaring steps, applied in order to the source program;
- The last chapter would define PolyMiniRust as a subset of our starting language, along with a typechecker
  for it and a reference interpreter adapted from MiniRust[^4].

In the same repository, though not part of the authoritative content (see next section),
would be an implementation of a type inference algorithm, a trait solver, and a borrow-checker,
so that the whole thing can run and execute realistic Rust programs.

That would be our spec.
This program would be a very carefully written
reference compiler/interpreter,
which we could eventually decide to be the source of truth for what Rust is.
We could run it on real Rust programs and compare its output against rustc.

[^4]: We could alternatively take MiniRust exactly as it is and describe a monomorphization step that tranforms
  PolyMiniRust into MiniRust by instantiating all the generics until everything is monomorphic.
  Depends which is easier.

### Out Of Scope: Type Inference, Trait Solving, Borrow-Checking

A important choice I'm making is to exclude
the "how" of type inference, trait solving and borrow-checking
from this spec document.
The idea is that the PolyMiniRust type-checker would be a sufficient constraint
for these algorithms: 
it will ensure that types match,
that each trait proof is a valid proof for the trait fact it
claims to prove, and something about borrow-checking[^5].

That way the algorithms can stay abstract:
there will be a desugaring step that says "some algorithm makes a choice for all ambiguous
types" and another that says "some algorithm constructs a trait proof for every trait fact",
and any choice is considered valid.

This does mean that two spec-compliant Rust compilers could
ascribe different behaviors to the same Rust program[^7].
To some degree that's inevitable: inference quirks are [explicitly out of
scope](https://blog.rust-lang.org/2014/10/30/Stability/) of our stability guarantees.
Until we're ready to guarantee more,
ambiguous programs can always be made unambiguous by adding type annotations.

There will still be a reference implementation of all these algorithms in the repository,
for the purpose of making the spec executable.
We could make them non-deterministic to avoid relying on particular choices they make
when we test the spec.

[^5]: It may be that we have to include a complete description of borrow-checking in the
  PolyMiniRust type-checker; I'm hoping we can instead have some explicit syntax like "this lifetime
  contains a loan to that place" that a borrow-checker would do the hard job of inferring and the
  type-checker would only have to check.
[^6]: That's called ["coherence"](https://smallcultfollowing.com/babysteps/blog/2015/01/14/little-orphan-impls/)
[^7]: In fact this even means that a type/trait inference algorithm that always returns an error would be
  spec-compliant. Specifying in which cases type/trait inference _must_ succeed seems hard and
  unnecessary to understand a program in practice since you typically already know that rustc
  accepts it.

### Out Of Scope: `std`, Crate Dependencies, FFI, ...

The spec I've described only captures a single pure Rust program.
This will probably assume that that program contains
appropriate definitions for the required lang items like builtin traits.
Anything beyond that I'm leaving for future work because that's already ambitious enough :)

### Stretch Goal: Interactive Desugarings

A really cute tool we could build on top of such a spec would be one
that allows you to selectively apply a chosen desugaring (e.g. "method resolution" or "temporary
lifetime extension") to a piece of code,
to help understand it.
This could even be a rust-analyzer code action.

## What We Have Today

There are a number of efforts that cover a lot of the space already,
though most are quite experimental:

- [MiniRust]: As described above, it's an interpreter for a bare-bones Rust that specifies the
  dynamic semantics of Rust. Pretty much done, we can use it as-is;
- [rust-via-desugarings]: An English-language sketch of a set of desugaring passes that go
  from real Rust to (Poly)MiniRust;
- [a-mir-formality]: That's the closest to an overarching "formal spec" effort: it's a project led by
  [Niko] to specify the type-system-related features of Rust. It uses MiniRust as its base language
  and includes a working borrow-checker, a trait solver, a surface language definition, and more;
- [Charon]: A tool meant to be an easy entrypoint for analysis tools. It uses rustc to translate
  a complete crate into a custom representation, which is pretty much PolyMiniRust (I based
  PolyMiniRust on it in fact)
  (disclaimer: working on Charon is my full-time paid job);
- [dictionary-passing-lambda-calculus]: a toy formal language + typechecker meant to capture the trait-related
  parts of the typesystem of PolyMiniRust, particularly the [circularity of trait
  proofs](https://nadrieril.github.io/blog/2026/05/14/when-can-traits-depend-on-themselves.html).
  I wrote this as part of the [dictionary-passing-style experiment][dict-experiment];
- [The Rust Reference][reference]: the only authoritative document of the bunch. This is an
  English-language description of a large proportion of Rust. It's the go-to document if there's
  doubt about a detail of Rust semantics. 

I don't know about you but to me they look like they're itching to be combined into a ~Megazord~
full specification.

## How We Get There

The trickiest part I think is the question
of getting the interaction of desugarings with type inference/trait solving right.
The first step I see is to chat with the mir-formality team
about this project and figure out how we can make the two projects work together.
I'm writing this blog post for that exact purpose.

Then I'll submit a [Project Goal](https://rust-lang.github.io/rust-project-goals/2026/index.html) and
see if the Project is keen for this to happen.

Once we get approval, there are a bunch of steps that can happen somewhat independently to get
us started:
- Start defining the main language (should we call it SpecRust?) and write up some interesting desugarings for it;
- Write a printer for SpecRust that generates valid Rust code so that we can test the resulting code
  using rustc;
- Integrate mir-formality as a type/trait oracle, or vice-versa add some desugaring steps to
  mir-formality, depending on how we decide to do things;
- Define PolyMiniRust in specr and write a toy typechecker for it;
- Write a PolyMiniRust -> MiniRust monomorphization pass, or adapt the MiniRust interpreter to
  PolyMiniRust, depending on what looks easiest;
- Write a Charon -> PolyMiniRust translator to enable testing/experimentation of PolyMiniRust;

Once these bases are in place, we'll get to the heart of the work:
coding an entire Rust compiler/interpreter from scratch :D
I expect a lot of the work can happen in parallel,
but that will be a gigantic undertaking regardless.
We'll also have to be careful not to implement any behavior that isn't currently guaranteed
by the [Reference][reference], or to get the lang team to approve the new behavior.

Success for this project would be
that over time people will turn to this
instead of the Reference as the de-facto authoritative document,
because of its precision and tinkerability.
That's the bet I'm taking!

### How You Can Help

Right now, I don't know!
But when the project goal gets accepted, there'll be a lot to do!
You can subscribe to my RSS feed for updates :p 
Otherwise I guess the
[#t-types/formality](https://rust-lang.zulipchat.com/#topics/channel/402470-t-types.2Fformality)
stream on Zulip is likely to be where things happen.

## Conclusion

I love about Rust that it gets many things not only right, but also makes them fun.
This vision is how I think we can make a formal spec for Rust fun too:
something tinkerable and accessible that gives an
even firmer grounding to our already-robust language.

I don't know about you but this approach feels so obvious in hindsight?
Back when I first heard of the idea of formally specifying Rust,
I had no idea where one would even start given how complex Rust is.
Now I can't imagine any other approach!
Let me know if you can, I'd be very curious.

I suspect I'm being overly naive about how easy this whole thing will be.
I will soon learn that actual Rust has a lot more moving parts than I ever wanted to know about!
I hope you'll join me in this adventure.

> I'd like to give a ton of credit to Ralf for showing us (with MiniRust) that precise semantics can
be accessible.
I'd also like to credit Niko for most of the ideas that led me to this blog post: of having
a tinkerable spec, of using desugarings, of integrating an executable spec into our language
development process, etc.
And I'd like to thank [lcnr] for bringing me onto the dictionary-passing-style experiment
and mentoring me through some gnarly trait stuffs.
I am grateful for your individual visions and for the joy it is to work with you.

[a-mir-formality]: https://github.com/rust-lang/a-mir-formality
[Charon]: https://github.com/AeneasVerif/charon
[dict-experiment]: https://rust-lang.github.io/rust-project-goals/2026/dictionary-passing-style-experiment.html
[dictionary-passing-lambda-calculus]: https://github.com/Nadrieril/dictionary-passing-lambda-calculus/
[lcnr]: https://github.com/lcnr
[MiniRust]: https://github.com/minirust/minirust 
[Niko]: https://github.com/nikomatsakis
[reference]: https://doc.rust-lang.org/reference/
[rust-via-desugarings]: https://nadrieril.github.io/rust-via-desugarings
[specr]: https://github.com/minirust/specr/
[types-team]: https://github.com/rust-lang/types-team
[UB]: https://rust-lang.github.io/unsafe-code-guidelines/glossary.html#undefined-behavior
