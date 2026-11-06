export const grammarIssueKey = (issue: GrammarIssue): string =>
    `${issue.text_index}:${issue.offset}:${issue.length}`;

export const applyGrammarFixesToText = (
    text: string,
    issues: GrammarIssue[],
    choices: Record<string, string> = {},
): string => {
    let result = text;
    [...issues]
        .sort((a, b) => b.offset - a.offset)
        .forEach((issue) => {
            const replacement = issue.replacements.length === 1
                ? issue.replacements[0]
                : choices[grammarIssueKey(issue)];
            if (!replacement) return;
            if (result.slice(issue.offset, issue.offset + issue.length) !== issue.original) return;
            result = result.slice(0, issue.offset) + replacement + result.slice(issue.offset + issue.length);
        });
    return result;
};

export const groupGrammarIssues = (issues: GrammarIssue[]): Map<number, GrammarIssue[]> => {
    const grouped = new Map<number, GrammarIssue[]>();
    issues.forEach((issue) => {
        const list = grouped.get(issue.text_index) || [];
        list.push(issue);
        grouped.set(issue.text_index, list);
    });
    return grouped;
};
