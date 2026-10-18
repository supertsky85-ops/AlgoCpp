"""Lightweight STL type inference for member completion.

AlgoCpp is not a compiler, so this module only recognises common container
declarations.  Given the code with comments and literals already blanked out
(``editor_features.scan``), it maps each visible variable to a container type
and exposes that type's member names.  The result is used to turn ``mp.`` into
map member suggestions instead of an alphabetical dump of every known word.
"""
import re

# Members are listed in the order shown in the popup: frequently used first.
MEMBERS = {
    'vector': ['size', 'empty', 'push_back', 'emplace_back', 'pop_back', 'back', 'front', 'at',
               'begin', 'end', 'rbegin', 'rend', 'insert', 'emplace', 'erase', 'resize',
               'reserve', 'capacity', 'shrink_to_fit', 'assign', 'clear', 'data', 'swap'],
    'deque': ['size', 'empty', 'push_back', 'emplace_back', 'push_front', 'emplace_front',
              'pop_back', 'pop_front', 'back', 'front', 'at', 'begin', 'end', 'rbegin', 'rend',
              'insert', 'emplace', 'erase', 'resize', 'clear', 'assign', 'swap'],
    'list': ['size', 'empty', 'push_back', 'emplace_back', 'push_front', 'emplace_front',
             'pop_back', 'pop_front', 'back', 'front', 'begin', 'end', 'rbegin', 'rend',
             'insert', 'emplace', 'erase', 'remove', 'remove_if', 'unique', 'sort', 'reverse',
             'merge', 'splice', 'resize', 'clear', 'assign', 'swap'],
    'forward_list': ['empty', 'push_front', 'emplace_front', 'pop_front', 'front', 'begin', 'end',
                     'before_begin', 'insert_after', 'emplace_after', 'erase_after', 'remove',
                     'remove_if', 'unique', 'sort', 'reverse', 'merge', 'splice_after', 'resize',
                     'clear', 'assign', 'swap'],
    'array': ['size', 'empty', 'at', 'front', 'back', 'data', 'begin', 'end', 'rbegin', 'rend',
              'fill', 'swap'],
    'set': ['size', 'empty', 'insert', 'emplace', 'erase', 'find', 'count', 'contains',
            'lower_bound', 'upper_bound', 'equal_range', 'begin', 'end', 'rbegin', 'rend',
            'clear', 'extract', 'merge', 'swap', 'key_comp', 'value_comp'],
    'multiset': ['size', 'empty', 'insert', 'emplace', 'erase', 'find', 'count', 'contains',
                 'lower_bound', 'upper_bound', 'equal_range', 'begin', 'end', 'rbegin', 'rend',
                 'clear', 'extract', 'merge', 'swap', 'key_comp', 'value_comp'],
    'unordered_set': ['size', 'empty', 'insert', 'emplace', 'emplace_hint', 'erase', 'find',
                      'count', 'contains', 'begin', 'end', 'cbegin', 'cend', 'clear', 'extract',
                      'merge', 'swap', 'bucket_count', 'load_factor', 'max_load_factor',
                      'rehash', 'reserve', 'hash_function', 'key_eq'],
    'unordered_multiset': ['size', 'empty', 'insert', 'emplace', 'emplace_hint', 'erase', 'find',
                           'count', 'contains', 'begin', 'end', 'cbegin', 'cend', 'clear',
                           'extract', 'merge', 'swap', 'bucket_count', 'load_factor',
                           'max_load_factor', 'rehash', 'reserve', 'hash_function', 'key_eq'],
    'map': ['size', 'empty', 'find', 'count', 'contains', 'lower_bound', 'upper_bound',
            'equal_range', 'insert', 'insert_or_assign', 'emplace', 'try_emplace', 'erase', 'at',
            'begin', 'end', 'rbegin', 'rend', 'clear', 'extract', 'merge', 'swap', 'key_comp',
            'value_comp'],
    'multimap': ['size', 'empty', 'find', 'count', 'contains', 'lower_bound', 'upper_bound',
                 'equal_range', 'insert', 'emplace', 'erase', 'begin', 'end', 'rbegin', 'rend',
                 'clear', 'extract', 'merge', 'swap', 'key_comp', 'value_comp'],
    'unordered_map': ['size', 'empty', 'find', 'count', 'contains', 'insert', 'insert_or_assign',
                      'emplace', 'try_emplace', 'erase', 'at', 'begin', 'end', 'cbegin', 'cend',
                      'clear', 'extract', 'merge', 'swap', 'bucket_count', 'load_factor',
                      'max_load_factor', 'rehash', 'reserve', 'hash_function', 'key_eq'],
    'unordered_multimap': ['size', 'empty', 'find', 'count', 'contains', 'insert', 'emplace',
                           'erase', 'begin', 'end', 'cbegin', 'cend', 'clear', 'extract', 'merge',
                           'swap', 'bucket_count', 'load_factor', 'max_load_factor', 'rehash',
                           'reserve', 'hash_function', 'key_eq'],
    'stack': ['size', 'empty', 'push', 'emplace', 'pop', 'top', 'swap'],
    'queue': ['size', 'empty', 'push', 'emplace', 'pop', 'front', 'back', 'swap'],
    'priority_queue': ['size', 'empty', 'push', 'emplace', 'pop', 'top', 'swap'],
    'bitset': ['size', 'test', 'set', 'reset', 'flip', 'count', 'all', 'any', 'none',
               'to_string', 'to_ulong', 'to_ullong'],
    'string': ['size', 'length', 'empty', 'clear', 'push_back', 'pop_back', 'append', 'assign',
               'insert', 'erase', 'replace', 'substr', 'find', 'rfind', 'find_first_of',
               'find_last_of', 'find_first_not_of', 'find_last_not_of', 'compare', 'c_str',
               'data', 'at', 'front', 'back', 'begin', 'end', 'rbegin', 'rend', 'reserve',
               'resize', 'shrink_to_fit', 'starts_with', 'ends_with', 'contains', 'swap'],
    'pair': ['first', 'second'],
}

# Inserted when a member is accepted.  Data members stay bare; calls get a
# shape that matches the usual STL signature so the caret lands inside it.
MEMBER_CALLS = {
    'size': 'size()', 'length': 'length()', 'empty': 'empty()', 'clear': 'clear()',
    'begin': 'begin()', 'end': 'end()', 'rbegin': 'rbegin()', 'rend': 'rend()',
    'cbegin': 'cbegin()', 'cend': 'cend()', 'front': 'front()', 'back': 'back()',
    'top': 'top()', 'data': 'data()', 'c_str': 'c_str()', 'capacity': 'capacity()',
    'push_back': 'push_back(x)', 'emplace_back': 'emplace_back(x)', 'pop_back': 'pop_back()',
    'push_front': 'push_front(x)', 'emplace_front': 'emplace_front(x)', 'pop_front': 'pop_front()',
    'push': 'push(x)', 'emplace': 'emplace(x)', 'pop': 'pop()',
    'insert': 'insert(x)', 'erase': 'erase(x)', 'find': 'find(x)', 'count': 'count(x)',
    'contains': 'contains(x)', 'lower_bound': 'lower_bound(x)', 'upper_bound': 'upper_bound(x)',
    'equal_range': 'equal_range(x)', 'insert_or_assign': 'insert_or_assign(key, value)',
    'try_emplace': 'try_emplace(key, value)', 'at': 'at(i)', 'resize': 'resize(n)',
    'reserve': 'reserve(n)', 'assign': 'assign(n, x)', 'append': 'append(s)',
    'replace': 'replace(pos, len, s)', 'substr': 'substr(pos, len)', 'find_first_of': 'find_first_of(s)',
    'find_last_of': 'find_last_of(s)', 'find_first_not_of': 'find_first_not_of(s)',
    'find_last_not_of': 'find_last_not_of(s)', 'compare': 'compare(s)', 'rfind': 'rfind(s)',
    'test': 'test(i)', 'set': 'set(i)', 'reset': 'reset()', 'flip': 'flip()',
    'fill': 'fill(x)', 'sort': 'sort()', 'reverse': 'reverse()', 'unique': 'unique()',
    'remove': 'remove(x)', 'remove_if': 'remove_if(pred)', 'merge': 'merge(other)',
    'splice': 'splice(pos, other)', 'splice_after': 'splice_after(pos, other)',
    'insert_after': 'insert_after(pos, x)', 'emplace_after': 'emplace_after(pos, x)',
    'erase_after': 'erase_after(pos)', 'before_begin': 'before_begin()',
    'key_comp': 'key_comp()', 'value_comp': 'value_comp()', 'bucket_count': 'bucket_count()',
    'load_factor': 'load_factor()', 'max_load_factor': 'max_load_factor()', 'rehash': 'rehash(n)',
    'hash_function': 'hash_function()', 'key_eq': 'key_eq()', 'extract': 'extract(key)',
    'to_string': 'to_string()', 'to_ulong': 'to_ulong()', 'to_ullong': 'to_ullong()',
    'starts_with': 'starts_with(s)', 'ends_with': 'ends_with(s)',
}

ALL_MEMBERS = tuple(dict.fromkeys(name for names in MEMBERS.values() for name in names))

def _skip_space(code, i):
    while i < len(code) and code[i].isspace():
        i += 1
    return i


def _template_end(code, i):
    """Return the offset just past the ``>`` matching the ``<`` at *i*."""
    depth = 0
    while i < len(code):
        char = code[i]
        if char == '<':
            depth += 1
        elif char == '>':
            depth -= 1
            if depth == 0:
                return i + 1
        elif char in ';{}':
            return None
        i += 1
    return None


def _declarators(code, i, limit):
    """Read ``a, *b, c[n]`` after a type, returning (name, offset) pairs."""
    found = []
    while i < limit:
        j = _skip_space(code, i)
        while j < limit and code[j] in '*&':
            j = _skip_space(code, j + 1)
        match = re.match(r'[A-Za-z_]\w*', code[j:limit])
        if not match:
            break
        name = match.group()
        after = _skip_space(code, j + match.end())
        if after >= limit:
            break
        if code.startswith('::', after):
            # A nested type such as map<int,int>::iterator, not a variable.
            return found
        if code[after] not in ';,=([{)>':
            break
        found.append((name, j))
        next_token = _skip_space(code, after)
        if next_token < limit and code[next_token] == ',':
            i = next_token + 1
            continue
        break
    return found


def declarations(code, limit=None):
    """Return ``(name, offset, type)`` for visible container declarations."""
    limit = len(code) if limit is None else max(0, min(len(code), limit))
    aliases = {}
    for match in re.finditer(r'\busing\s+([A-Za-z_]\w*)\s*=\s*(?:std\s*::\s*)?([A-Za-z_]\w*)', code):
        if match.group(2) in MEMBERS:
            aliases[match.group(1)] = match.group(2)
    type_names = set(MEMBERS) | set(aliases)
    found = []
    for match in re.finditer(r'\b(?P<name>[A-Za-z_]\w*)\b', code[:limit]):
        word = match.group('name')
        if word not in type_names:
            continue
        start = match.start()
        if start and code[start - 1] in '.>':
            continue
        if start and code[start - 1] == ':' and not (start >= 2 and code[start - 2] == ':'):
            continue
        # A declaration's type must be usable: skip return-type-like words
        # followed by another identifier?  The declarator check below decides.
        j = _skip_space(code, match.end())
        if j < limit and code[j] == '<':
            end = _template_end(code, j)
            if end is None or end > limit:
                continue
            j = end
        after_type = _skip_space(code, j)
        if code.startswith('::', after_type):
            continue
        kind = aliases.get(word, word)
        for name, offset in _declarators(code, after_type, limit):
            found.append((name, offset, kind))
    return found


def infer(code, offset):
    """Map variable names visible before *offset* to a container type."""
    types = {}
    for name, position, kind in declarations(code, offset):
        if position < offset:
            # Later declarations shadow earlier ones, which is the common
            # case inside one function and harmless across functions.
            types[name] = kind
    return types


def analyze(code, offset):
    """Describe the member access ending at *offset*.

    Returns a dict with ``receiver`` (identifier before ``.``/``->``), ``type``
    (inferred container or None), ``prefix`` (the partially typed member) and
    ``types`` (all inferred variables).
    """
    offset = max(0, min(len(code), offset))
    types = infer(code, offset)
    context = re.search(r'([A-Za-z_]\w*)[ \t]*(\.|->)[ \t]*([A-Za-z_]\w*)?[ \t]*\Z', code[:offset])
    if not context:
        return {'receiver': None, 'type': None, 'prefix': '', 'types': types}
    receiver = context.group(1)
    return {'receiver': receiver, 'type': types.get(receiver),
            'prefix': context.group(3) or '', 'types': types}


def members(type_name):
    return MEMBERS.get(type_name, ())


def member_snippet(name):
    return MEMBER_CALLS.get(name, name if name in ('first', 'second') else name + '()')
