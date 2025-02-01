def get_unclosed_code_block_language(message_chunk: str) -> str | None:
    """\
    Given an input string representing Discord message content,
    find the last unclosed code block and return its language.

    For example, for the following text: "```python\nprint('Hello, World!')",
    the function should return "python".

    NOTE: An empty string is considered as a valid language.
    """
    # Even means all blocks were closed correctly.
    # TODO: this is not really true because of "\```"
    if message_chunk.count("```") % 2 == 0:
        return None

    # Find the last block and get its language using the format ```<language>\n
    block_index = message_chunk.rfind("```")
    remaining_slice = message_chunk[block_index:]
    slice_split = remaining_slice.split("\n", maxsplit=1)

    # NOTE: This considers edge cases where the block stats at the last
    # line of the message.
    language = slice_split[0].removeprefix("```").strip()
    return language


def split_message_into_chunks(message: str, *, max_length: int) -> list[str]:
    # TODO: is this the exact same impl as a normal chunk function?
    if len(message) <= max_length:
        return [message]
    else:
        # split on last space before max_length
        split_index = message.rfind(" ", 0, max_length)
        if split_index == -1:
            split_index = max_length
        return [message[:split_index]] + split_message_into_chunks(
            message[split_index:], max_length=max_length
        )


def smart_split_message_into_chunks(message: str, *, max_length: int) -> list[str]:
    """Like `split_message_into_chunks`, but also considers code blocks."""

    split_messages = split_message_into_chunks(
        message,
        # Magic number to account for MD code embed headers.
        max_length=max_length - 15,
    )
    output_messages = []
    code_block_language = None

    for message_chunk in split_messages:
        if code_block_language is not None:
            message_chunk = f"```{code_block_language}\n" + message_chunk
            code_block_language = None

        code_block_language = get_unclosed_code_block_language(message_chunk)
        if code_block_language is not None:
            message_chunk += "\n```"

        output_messages.append(message_chunk)

    return output_messages
