"""Tab completion for bash, zsh, fish and PowerShell.

Each shell script calls ``voicestudio __complete <words…>`` on every Tab; the
last word is the one being completed (empty after a space). Command names come
from a lightweight index of command paths, so completing them builds no
parsers at all. Options and fixed choices build the parser of the one command
being typed. Nothing here contacts a backend or starts the app.
"""

# Global options that take a value: their value is not a command word.
VALUE_OPTIONS = {'--url', '--timeout', '-o', '--output'}


def _index():
    """Visible command paths -> description, from data only (no argparse)."""
    from .__main__ import CUSTOM, DESCRIPTIONS, GROUPS, HIDDEN, RENAMED
    from .registry import COMMANDS
    commands = {}
    for spec in COMMANDS:
        if spec.name not in CUSTOM and spec.name not in HIDDEN:
            commands[RENAMED.get(spec.name, spec.name)] = spec.description
    for name in CUSTOM - HIDDEN:
        commands[RENAMED.get(name, name)] = DESCRIPTIONS.get(name, '')
    groups = {}
    for path in commands:
        words = path.split()
        for n in range(1, len(words)):
            group = ' '.join(words[:n])
            groups.setdefault(group, GROUPS.get(group, ''))
    return commands, groups


def _children(prefix, commands, groups):
    depth = len(prefix)
    found = {}
    for path, description in [*groups.items(), *commands.items()]:
        words = path.split()
        if len(words) == depth + 1 and words[:depth] == prefix:
            found.setdefault(words[-1], description)
    return found


def _parser(path):
    from .__main__ import build_parser
    root, nodes = build_parser(only=tuple(path))
    return nodes.get(tuple(path)) or root


def _options(parser):
    import argparse
    found = {}
    for action in parser._actions:
        if action.help == argparse.SUPPRESS:
            continue
        for flag in action.option_strings:
            if flag.startswith('--') or not any(o.startswith('--') for o in action.option_strings):
                found[flag] = (action.help or '').split(';')[0]
    return found


def complete(words):
    """Candidates as ``value<TAB>description`` lines for the word being typed."""
    words = list(words) or ['']
    current, before = words[-1], words[:-1]
    commands, groups = _index()
    path, skip, previous = [], False, None
    for word in before:
        if skip:
            skip = False
            previous = None
            continue
        if word.startswith('-'):
            skip = word in VALUE_OPTIONS
            previous = word
            continue
        if ' '.join([*path, word]) in commands or ' '.join([*path, word]) in groups:
            path.append(word)
        elif ' '.join(path) not in commands:
            return []  # an unknown (or former, unlisted) name: nothing sensible to offer
        previous = None
    joined = ' '.join(path)
    is_command = joined in commands
    if skip and previous:
        return []  # a value the user supplies (URL, file, number): let the shell offer files
    if current.startswith('-'):
        options = _options(_parser(path)) if is_command else {
            '--help': 'Show help', '--json': 'Print machine-readable JSON', '--version': 'Show the installed version'}
        return [f'{flag}\t{text}' for flag, text in sorted(options.items()) if flag.startswith(current)]
    if not is_command or joined in groups:
        children = _children(path, commands, groups)
        if children:
            return [f'{name}\t{text}' for name, text in sorted(children.items()) if name.startswith(current)]
    if is_command:
        parser = _parser(path)
        for action in parser._actions:
            if action.choices and not isinstance(action.choices, dict):
                if previous in action.option_strings or (not action.option_strings and previous is None):
                    return [str(choice) for choice in action.choices if str(choice).startswith(current)]
    return []


BASH = r'''# VoiceStudio Tab completion for bash: eval "$(voicestudio completion bash)"
_voicestudio() {
    local IFS=$'\n'
    COMPREPLY=($(voicestudio __complete "${COMP_WORDS[@]:1:COMP_CWORD}" 2>/dev/null | cut -f1))
}
complete -o default -F _voicestudio voicestudio
'''

ZSH = r'''# VoiceStudio Tab completion for zsh: eval "$(voicestudio completion zsh)"
_voicestudio() {
    local -a items
    local line
    for line in "${(@f)$(voicestudio __complete "${(@)words[2,CURRENT]}" 2>/dev/null)}"; do
        [[ -n $line ]] && items+=("${${line%%$'\t'*}//:/\\:}:${line#*$'\t'}")
    done
    if (( ${#items} )); then _describe 'voicestudio' items; else _files; fi
}
(( $+functions[compdef] )) || { autoload -Uz compinit && compinit; }
compdef _voicestudio voicestudio
'''

FISH = r'''# VoiceStudio Tab completion for fish: voicestudio completion fish | source
complete -c voicestudio -e
complete -c voicestudio -a '(voicestudio __complete (commandline -opc)[2..-1] (commandline -ct) 2>/dev/null)'
'''

POWERSHELL = r'''# VoiceStudio Tab completion for PowerShell: voicestudio completion powershell | Out-String | Invoke-Expression
Register-ArgumentCompleter -Native -CommandName voicestudio -ScriptBlock {
    param($wordToComplete, $commandAst, $cursorPosition)
    $words = @($commandAst.CommandElements | Select-Object -Skip 1 |
        Where-Object { $_.Extent.EndOffset -le $cursorPosition } | ForEach-Object { $_.ToString() })
    if ($wordToComplete -eq '') { $words += '' }
    voicestudio __complete @words 2>$null | ForEach-Object {
        $name, $description = $_ -split "`t", 2
        if (-not $description) { $description = $name }
        [System.Management.Automation.CompletionResult]::new($name, $name, 'ParameterValue', $description)
    }
}
'''

SCRIPTS = {'bash': BASH, 'zsh': ZSH, 'fish': FISH, 'powershell': POWERSHELL}


def script(shell):
    return SCRIPTS[shell]
