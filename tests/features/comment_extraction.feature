Feature: Extract inline comments from Word documents

  Scenario Outline: Extract commented content to standard output
    Given a "<fixture_name>" fixture document
    When I run the extractor CLI on the document
    Then the command exits successfully
    And standard output matches the "<snapshot_name>" snapshot
    And standard error is empty

    Examples:
      | fixture_name            | snapshot_name                  |
      | simple-comment          | simple_comment_stdout          |
      | multi-run-comment       | multi_run_comment_stdout       |
      | cross-paragraph-comment | cross_paragraph_comment_stdout |

  Scenario: Write extracted Markdown to an output file
    Given a "simple-comment" fixture document
    When I run the extractor CLI with an output file
    Then the command exits successfully
    And the output file matches the "simple_comment_output_file" snapshot
    And standard output is empty
    And standard error contains "Wrote Markdown"

  Scenario: Reject a missing input path
    Given a missing fixture document path
    When I run the extractor CLI on the document
    Then the command exits with an error
    And standard error contains "does not exist"

  Scenario: Reject an invalid input extension
    Given an invalid-extension document path
    When I run the extractor CLI on the document
    Then the command exits with an error
    And standard error contains "must use the .docx extension"

  Scenario: Reject a directory input path
    Given a directory document path
    When I run the extractor CLI on the document
    Then the command exits with an error
    And standard error contains "is not a file"

  Scenario: Report a corrupt Word package cleanly
    Given a corrupt Word document path
    When I run the extractor CLI on the document
    Then the command exits with an error
    And standard error contains "Could not extract the Word document"
