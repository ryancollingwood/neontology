**Jules Execution Prompt**

**Objective:** You are an autonomous coding agent. Your goal is to pick up the next available, actionable task in the repository and implement a robust, tested solution.

**Instructions:**

1. **Find the Next Task:**  
   * Review `docs/ladybugdb/implementation/README.md` in the root of the repository. This file contains sorted list of implementation steps.
   * Identify the next implementation step to be done
   * For the identified step, read it's associated document
   * Then review the code and validate that the step has not as yet been implemented
    * If you determine the step has been implemented update `docs/ladybugdb/implementation/README.md` to reflect this and move onto the next step - repeating the verification process until you find step that has not been implemented.
2. **Capture Learnings and Decisions:**  
   * As you proceed you will capture any thoughts, observations, decisions, or deviations from the original instructions in an markdown file in the same directory as the step document
    * If instance if the step document name is `01-config-and-skeleton.md` then create/update a document `01-working-notes.md` in the same directory
2. **Adhere to Guidelines:**  
   * Review the `docs/.development.md` file located at the root of the repository.  
   * You **must strictly adhere** to all coding standards, architectural rules, testing requirements, and formatting guidelines specified within that document while implementing your solution. 
3. **Finalize the Task:**  
   * Once the step is complete and verified, update `docs/ladybugdb/implementation/README.md` to reflect this.  
